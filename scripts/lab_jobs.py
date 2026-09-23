#!/usr/bin/env python3
"""Detached, bounded jobs with durable reports and lifecycle drain."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import selectors
import subprocess
import sys
import time
import uuid

from lab_common import SCRIPTS, atomic, clean_environment, git, kit, lock, path_env, runtime


def root():
    p = path_env('AGENT_JOBS_DIR', Path.home() / 'agent-jobs')
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def directory(job):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', job):
        raise ValueError('Invalid job id')
    return root() / job


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (FileNotFoundError, ProcessLookupError):
        return None


def read(job):
    d = directory(job)
    m = json.loads((d / 'result.json').read_text())
    if m['state'] in ('running', 'queued') and time.time() - m['created'] > 10:
        if not m.get('supervisor_pid') or identity(m['supervisor_pid']) != m.get('supervisor_start'):
            m.update(state='interrupted', finished=time.time(), reason='supervisor-lost')
            atomic(d / 'result.json', m)
    return m


def all_jobs():
    return [read(p.parent.name) for p in sorted(root().glob('*/result.json'))]


def start(args):
    command = args.command
    if command and command[0] == '--': command = command[1:]
    if not command: raise ValueError('A command after -- is required')
    for name in args.pass_env:
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name) or name not in os.environ:
            raise ValueError(f'Explicit environment variable is unavailable or invalid: {name}')
    if args.image and (args.image.startswith('-') or not re.fullmatch(r'[A-Za-z0-9._/:@-]+', args.image)):
        raise ValueError('Invalid container image')
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir(): raise ValueError('Invalid working directory')
    with lock(kit() / 'jobs.lock'):
        state = runtime()['state']
        if state != 'SAFE' and not (state == 'UNKNOWN' and args.allow_unknown_runtime):
            raise ValueError(f'New jobs refused: lifecycle={state}')
        if any(j['state'] in ('queued', 'running') and j['cwd'] == str(cwd) for j in all_jobs()):
            raise ValueError('This workspace already has an active managed job')
        job = time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        d = directory(job); d.mkdir(mode=0o700)
        info = {'id': job, 'cwd': str(cwd), 'command_name': Path(command[0]).name, 'created': time.time(),
                'state': 'queued', 'timeout': args.timeout, 'sha': git(cwd, 'rev-parse', 'HEAD', check=False),
                'dirty': bool(git(cwd, 'status', '--porcelain', check=False)), 'run_id': os.getenv('GITHUB_RUN_ID'),
                'image': args.image, 'memory': args.memory, 'cpus': args.cpus,
                'grace_seconds': args.grace_seconds,
                'max_log_bytes': args.max_log_mb * 1024 * 1024,
                'passed_environment': sorted(set(args.pass_env)), 'network': args.network}
        atomic(d / 'command.json', {'argv': command})
        atomic(d / 'result.json', info)
        env = clean_environment()
        for k in ('AGENT_JOBS_DIR', 'AGENT_KIT_CACHE_DIR'):
            if k in os.environ: env[k] = os.environ[k]
        for k in info['passed_environment']:
            env[k] = os.environ[k]
        env['RUNNER_TRACKING_ID'] = 'agent-lab-job-' + job
        with (d / 'supervisor.log').open('w') as log:
            process = subprocess.Popen([sys.executable, __file__, '_worker', job], env=env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        info.update(supervisor_pid=process.pid, supervisor_start=identity(process.pid))
        atomic(d / 'result.json', info)
    print(job)
    return job


def terminate_group(pid):
    try: os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError: pass


def process_rss(pid):
    """Sample RSS for the entire command process group (approximate, shared pages included)."""
    total = 0
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            values = path.read_text().rsplit(')', 1)[1].split()
            if int(values[2]) == pid:
                total += int(values[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError): pass
    return total


def worker(job):
    d = directory(job)
    # Serialize startup with creator and drain; do not race queued metadata.
    with lock(kit() / 'jobs.lock'):
        info = read(job)

    # Keep the job HOME isolated, but preserve only the non-secret paths needed
    # for transparent GitHub authentication. The token itself remains in gh's
    # runner-user credential store and is never copied into the job environment.
    runner_home = Path(os.environ.get('HOME', str(Path.home()))).expanduser().resolve()
    gh_config_dir = Path(os.environ.get(
        'GH_CONFIG_DIR', str(runner_home / '.config/gh'))).expanduser().resolve()
    github_git_config = Path(os.environ.get(
        'AGENT_GITHUB_JOB_GIT_CONFIG',
        str(runner_home / '.config/agent-lab/github-auth/gitconfig'))).expanduser().resolve()

    env = clean_environment()
    (d / 'home').mkdir(exist_ok=True); (d / 'tmp').mkdir(exist_ok=True)
    env.update(HOME=str(d / 'home'), TMPDIR=str(d / 'tmp'), AGENT_JOB_ID=job,
               AGENT_JOB_OUTPUT_DIR=str(d), RUNNER_TRACKING_ID='agent-lab-job-' + job)
    if gh_config_dir.is_dir() and github_git_config.is_file():
        env['GH_CONFIG_DIR'] = str(gh_config_dir)
        env['GIT_CONFIG_GLOBAL'] = str(github_git_config)
    for name in info.get('passed_environment', []):
        if name in os.environ: env[name] = os.environ[name]
    command_file = d / 'command.json'
    command = json.loads(command_file.read_text())['argv']
    container = 'agent-lab-' + job
    if info['image']:
        # No host credentials, Docker socket, published ports or privileged mode.
        command = ['docker', 'run', '--name', container, '--network=' + info['network'], '--cap-drop=ALL',
                   '--security-opt=no-new-privileges', '--pids-limit=256',
                   '--memory=' + info['memory'], '--memory-swap=' + info['memory'],
                   '--cpus=' + str(info['cpus']), '--user', f'{os.getuid()}:{os.getgid()}',
                   '--mount', f'type=bind,src={info["cwd"]},dst=/workspace',
                   '--workdir=/workspace', '--env=HOME=/tmp',
                   *[item for name in info.get('passed_environment', []) for item in ('--env', name)],
                   info['image'], *command]
    versions = {}
    for tool, flags in [('node', ['--version']), ('npm', ['--version']), ('python3', ['--version']), ('git', ['--version'])]:
        try:
            versions[tool] = subprocess.check_output([tool, *flags], env=env, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
        except (OSError, subprocess.SubprocessError): versions[tool] = 'unavailable'
    info.update(state='running', started=time.time(), versions=versions, platform=os.uname().release)
    peak = 0; reason = 'completed'; stopping = None
    try:
        with (d / 'output.log').open('wb') as log:
            # Keep this dependency-free: fresh runner images do not guarantee GNU time.
            p = subprocess.Popen(command, cwd=info['cwd'], env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            info.update(command_pid=p.pid, command_start=identity(p.pid))
            atomic(d / 'result.json', info)
            poller = selectors.DefaultSelector(); poller.register(p.stdout, selectors.EVENT_READ)
            written = 0; truncated = False
            while p.poll() is None or poller.get_map():
                for key, _ in poller.select(0.2):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        poller.unregister(key.fileobj); continue
                    room = max(0, info['max_log_bytes'] - written)
                    if room:
                        log.write(chunk[:room]); written += min(room, len(chunk))
                    if len(chunk) > room: truncated = True
                peak = max(peak, process_rss(p.pid))
                if stopping is None and ((d / 'cancel').exists() or time.time()-info['started'] >= info['timeout']):
                    reason = 'cancelled' if (d / 'cancel').exists() else 'timeout'
                    stopping = time.time(); terminate_group(p.pid)
                    if info['image']:
                        subprocess.run(['docker', 'stop', '-t', '5', container], capture_output=True, timeout=15)
                if stopping and time.time()-stopping > info.get('grace_seconds', 10):
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            code = p.wait()
            terminate_group(p.pid)  # Do not leave daemonized descendants in this group.
        if info['image']:
            inspect = subprocess.run(['docker', 'inspect', '--format', '{{json .State}}', container], capture_output=True, text=True)
            if inspect.returncode == 0: info['container_state'] = json.loads(inspect.stdout)
        info.update(state='success' if code == 0 and reason == 'completed' else 'failed',
                    reason=reason, exit_code=code, log_truncated=truncated)
    except Exception as e:
        info.update(state='failed', reason='launch-error', error=str(e), exit_code=None)
    finally:
        if info['image']:
            subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info.update(finished=time.time(), peak_group_rss_bytes=peak)
        command_file.unlink(missing_ok=True)
        info['artifacts'] = sorted(path.name for path in d.iterdir()
                                   if path.is_file() and path.name != 'result.json')
        atomic(d / 'result.json', info)


def drain(seconds):
    with lock(kit() / 'jobs.lock'):
        (kit() / 'draining').touch()
    deadline = time.monotonic() + seconds
    while True:
        active = [j for j in all_jobs() if j['state'] in ('queued', 'running')]
        if not active: return
        if time.monotonic() >= deadline:
            for j in active: (directory(j['id']) / 'cancel').touch()
            for _ in range(40):
                if not any(j['state'] in ('queued', 'running') for j in all_jobs()): return
                time.sleep(0.25)
            raise RuntimeError('Jobs did not drain; checkpoint will be marked best-effort')
        time.sleep(0.5)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest='action', required=True)
    a = s.add_parser('start'); a.add_argument('--cwd', default=os.getcwd())
    a.add_argument('--timeout', type=int, default=1800); a.add_argument('--allow-unknown-runtime', action='store_true')
    a.add_argument('--grace-seconds', type=int, default=10)
    a.add_argument('--max-log-mb', type=int, default=20)
    a.add_argument('--pass-env', action='append', default=[])
    a.add_argument('--network', choices=('none','bridge'), default='none')
    a.add_argument('--image'); a.add_argument('--memory', default='512m'); a.add_argument('--cpus', type=float, default=1)
    a.add_argument('command', nargs=argparse.REMAINDER)
    s.add_parser('list')
    for name in ('status', 'logs', 'stop', 'wait', '_worker'):
        a = s.add_parser(name); a.add_argument('id')
    a = s.add_parser('drain'); a.add_argument('--seconds', type=int, default=900)
    args = p.parse_args()
    if args.action == 'start':
        if args.timeout < 1 or args.max_log_mb < 1 or not 1 <= args.grace_seconds <= 60:
            raise ValueError('timeout/max-log-mb must be positive and grace-seconds must be 1..60')
        start(args)
    elif args.action == '_worker': worker(args.id)
    elif args.action == 'list': print(json.dumps(all_jobs(), indent=2))
    elif args.action == 'status': print(json.dumps(read(args.id), indent=2))
    elif args.action == 'logs': print((directory(args.id) / 'output.log').read_text()[-20000:])
    elif args.action == 'stop': (directory(args.id) / 'cancel').touch()
    elif args.action == 'drain': drain(args.seconds)
    elif args.action == 'wait':
        while (r := read(args.id))['state'] in ('queued', 'running'): time.sleep(0.25)
        print(json.dumps(r, indent=2)); sys.exit(0 if r['state'] == 'success' else 1)


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'Job error: {e}', file=sys.stderr); sys.exit(1)
