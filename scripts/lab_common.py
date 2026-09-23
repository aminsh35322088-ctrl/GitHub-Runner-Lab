"""Small, dependency-free primitives shared by Lab commands."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parent


def path_env(name, default):
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


def kit():
    return path_env('AGENT_KIT_CACHE_DIR', Path.home() / '.cache/agent-runner-kit')


def workspace_root():
    return path_env('AGENT_WORKSPACE_ROOT', Path.home() / 'agent-workspaces')


def run(args, cwd=None, check=True, **kw):
    return subprocess.run([str(x) for x in args], cwd=cwd, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def git(repo, *args, check=True):
    return run(['git', '-C', repo, *args], check=check).stdout.decode().strip()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(data, indent=2) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def runtime():
    file = kit() / 'runtime.env'
    if not file.exists():
        return {'state': 'UNKNOWN', 'remaining': 0}
    values = dict(line.split('=', 1) for line in file.read_text().splitlines() if '=' in line)
    remaining = int(values['HANDOFF_EPOCH']) - time.time()
    state = 'SAFE'
    if remaining <= 0:
        state = 'HANDOFF_DUE'
    elif (kit() / 'handoff.request').exists() or (kit() / 'draining').exists():
        state = 'RESTART_REQUESTED'
    elif remaining <= int(values.get('AUTO_HANDOFF_MINUTES', 20)) * 60:
        state = 'RESTART_WINDOW'
    return {'state': state, 'remaining': max(0, remaining)}


def clean_environment():
    # This prevents accidental inheritance, not hostile same-user access.
    allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TZ', 'TERM',
               'TMPDIR', 'CI', 'RUNNER_OS', 'RUNNER_ARCH', 'AGENT_WORKSPACE_ROOT',
               'AGENT_KIT_CACHE_DIR', 'AGENT_PROJECT_CACHE_ROOT', 'GH_CONFIG_DIR',
               'AGENT_GITHUB_JOB_GIT_CONFIG'}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_CONFIG_NOSYSTEM='1')
    return env
