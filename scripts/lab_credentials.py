#!/usr/bin/env python3
"""On-demand credentials for trusted foreground commands.

A user submits a token through a one-time, tailnet-only web page; `run NAME -- COMMAND`
passes it only to an explicitly allowed executable. Raw-token output is redacted and
bounded. This is not an OS sandbox: use only trusted commands and keep session data on tmpfs.
"""
import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lab_common import SCRIPTS, atomic

NAME = re.compile(r'^[a-z][a-z0-9-]{1,40}$')
ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,127}$')
CRED_MARKER = re.compile(r'(^|_)(TOKEN|SECRET|PASSWORD|PASS|KEY|APIKEY|CREDENTIAL|AUTH|BEARER|JWT)(_|$)|(TOKEN|SECRET|APIKEY)$')
COMMAND_NAME = re.compile(r'^[A-Za-z0-9._+-]{1,64}$')
UNSAFE_COMMANDS = frozenset({'sh', 'bash', 'dash', 'zsh', 'fish', 'ash', 'cmd', 'powershell', 'pwsh',
                             'python', 'python2', 'python3', 'pypy', 'node', 'deno', 'bun', 'ruby',
                             'perl', 'php', 'env', 'xargs', 'busybox', 'npm', 'npx', 'pnpm', 'yarn',
                             'bunx', 'make', 'just', 'task', 'go', 'cargo', 'gradle', 'mvn',
                             'git', 'docker', 'podman', 'wrangler'})
SAFE_ENV_NAMES = frozenset({'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ',
                            'TERM', 'TMPDIR', 'CI', 'NO_COLOR', 'FORCE_COLOR'})
def safe_path():
    # Include conventional user-installed CLI directories, but never inherit an arbitrary caller PATH.
    entries = ['/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin', '/sbin', '/bin']
    home = Path.home()
    entries.extend(str(home / item) for item in ('.local/bin', '.npm-global/bin', '.npm/bin')
                   if (home / item).is_dir())
    return os.pathsep.join(entries)


SAFE_PATH = safe_path()
REQUEST_TTL = 900
LIFETIMES = {'session': None, '1h': 3600, '24h': 86400}
INTEGRATIONS = SCRIPTS.parent / 'config' / 'integrations'


def now():
    return time.time()


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cred_dir():
    configured = os.environ.get('AGENT_CRED_DIR')
    if configured:
        base = Path(configured).expanduser().resolve()
        if not base.is_relative_to(Path('/dev/shm').resolve()):
            raise ValueError('AGENT_CRED_DIR must be on /dev/shm tmpfs; disk-backed credential storage is refused')
    else:
        runtime = Path('/dev/shm')
        if not runtime.is_dir():
            raise ValueError('tmpfs /dev/shm is unavailable; refusing to store session credentials on disk')
        base = runtime / f'agent-lab-cred-{os.getuid()}'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    for sub in ('requests', 'store'):
        (base / sub).mkdir(exist_ok=True, mode=0o700)
    return base


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_template(integration, env=None, allow=None):
    if integration == 'custom':
        if not env or not ENV_NAME.fullmatch(env) or not CRED_MARKER.search(env):
            raise ValueError('custom integrations need --env with a credential-style name such as MY_SERVICE_TOKEN')
        if not allow or not all(COMMAND_NAME.fullmatch(c) and '/' not in c and '\\\\' not in c
                                and c.lower() not in UNSAFE_COMMANDS for c in allow):
            raise ValueError('custom integrations need a safe, plain executable name; shells and interpreters are refused')
        return {'name': 'custom', 'env': env, 'allowed_commands': sorted(set(allow)),
                'guidance': 'Custom credential: review the allowed commands carefully before submitting.'}
    if not NAME.fullmatch(integration):
        raise ValueError('Invalid integration name')
    data = read_json(INTEGRATIONS / f'{integration}.json')
    if not data:
        raise ValueError(f'Unknown integration: {integration} (add config/integrations/{integration}.json or use custom)')
    if not ENV_NAME.fullmatch(data.get('env', '')) or not data.get('allowed_commands'):
        raise ValueError('Integration template is invalid')
    return data


@contextlib.contextmanager
def credential_name_lock(name):
    lock_dir = cred_dir() / 'requests' / '.locks'
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    path = lock_dir / f'name-{sha(name)}.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def new_request(name, template, purpose=''):
    if not NAME.fullmatch(name):
        raise ValueError('Credential name must be lowercase letters, digits and dashes')
    with credential_name_lock(name):
        if load_credential(name):
            raise ValueError(f'Credential {name} already exists; revoke it before requesting a replacement')
        pending = any(meta and meta.get('name') == name and meta.get('state') in ('pending', 'processing')
                      and now() <= meta.get('expires', 0)
                      for meta in (read_json(p) for p in (cred_dir() / 'requests').glob('*.json')))
        if pending:
            raise ValueError(f'A request for credential {name} is already pending')
        nonce = secrets.token_urlsafe(32)
        rid = secrets.token_hex(8)
        created = now()
        meta = {'id': rid, 'name': name, 'integration': template['name'], 'env': template['env'],
                'allowed_commands': template['allowed_commands'], 'purpose': (purpose or '')[:200],
                'validate': template.get('validate'), 'help_url': template.get('help_url'),
                'guidance': template.get('guidance'), 'nonce_sha256': sha(nonce), 'created': created,
                'expires': created + REQUEST_TTL, 'state': 'pending', 'attempts': 0, 'csrf': None}
        atomic(cred_dir() / 'requests' / f'{rid}.json', meta)
        return rid, nonce


def load_request(rid):
    if not re.fullmatch(r'[0-9a-f]{16}', rid or ''):
        return None
    return read_json(cred_dir() / 'requests' / f'{rid}.json')


@contextlib.contextmanager
def request_lock(rid):
    if not re.fullmatch(r'[0-9a-f]{16}', rid or ''):
        raise ValueError('Invalid credential request id')
    lock_dir = cred_dir() / 'requests' / '.locks'
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    path = lock_dir / f'{rid}.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save_request(meta):
    atomic(cred_dir() / 'requests' / f"{meta['id']}.json", meta)


def request_usable(meta, nonce):
    """True only for a pending, unexpired request whose one-time key matches."""
    if not meta or meta.get('state') != 'pending' or now() > meta['expires']:
        return False
    return hmac.compare_digest(meta.get('nonce_sha256', ''), sha(nonce or ''))


def validate_value(meta, value):
    """Optionally verify the token against the provider. Returns (ok, message)."""
    spec = meta.get('validate')
    if not spec:
        return True, 'accepted (not verified)'
    import urllib.request
    request = urllib.request.Request(spec['url'], headers={'Authorization': 'Bearer ' + value, 'User-Agent': 'agent-lab'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read(65536))
    except Exception:
        return False, 'The provider rejected the token or could not be reached.'
    if all(body.get(k) == v for k, v in spec.get('expect', {}).items()):
        return True, 'verified with the provider'
    return False, 'The provider did not accept this token.'


def store_credential(meta, value, lifetime):
    ttl = LIFETIMES[lifetime]
    created = now()
    record = {'name': meta['name'], 'env': meta['env'], 'integration': meta['integration'],
              'allowed_commands': meta['allowed_commands'], 'value': value, 'created': created,
              'lifetime': lifetime, 'expires': None if ttl is None else created + ttl}
    atomic(cred_dir() / 'store' / f"{meta['name']}.json", record)


def credential_path(name):
    if not NAME.fullmatch(name or ''):
        raise ValueError('Invalid credential name')
    return cred_dir() / 'store' / f'{name}.json'


def load_credential(name):
    path = credential_path(name)
    record = read_json(path)
    if not record:
        return None
    if record['expires'] is not None and now() > record['expires']:
        path.unlink(missing_ok=True)
        return None
    return record


def public_base():
    explicit = os.environ.get('AGENT_CRED_PUBLIC_URL')
    if explicit:
        from urllib.parse import urlsplit
        parsed = urlsplit(explicit)
        if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.lower().endswith('.ts.net')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('AGENT_CRED_PUBLIC_URL must be an HTTPS Tailscale *.ts.net origin without a path')
        return explicit.rstrip('/')
    try:
        out = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10, check=True).stdout
        dns = json.loads(out)['Self']['DNSName'].rstrip('.')
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        raise ValueError('No tailnet address available; is Tailscale connected?')
    return f"https://{dns}:{os.environ.get('AGENT_CRED_TS_PORT', '8443')}"


def panel_port():
    return int(os.environ.get('AGENT_CRED_PANEL_PORT', '8765'))


def panel_alive():
    info = read_json(cred_dir() / 'panel.json')
    if not info:
        return None
    try:
        os.kill(info['pid'], 0)
        cmdline = Path(f"/proc/{info['pid']}/cmdline").read_bytes()
    except (OSError, KeyError, TypeError):
        return None
    if b'lab_cred_panel.py' not in cmdline or b'serve' not in cmdline:
        return None
    return info


def tailscale_serve(action):
    if os.environ.get('AGENT_CRED_TAILSCALE', 'serve') == 'off':
        return
    port = os.environ.get('AGENT_CRED_TS_PORT', '8443')
    args = ['tailscale', 'serve', '--bg', f'--https={port}', f'http://127.0.0.1:{panel_port()}'] if action == 'on' \
        else ['tailscale', 'serve', f'--https={port}', 'off']
    for prefix in ([], ['sudo', '-n']):
        result = subprocess.run(prefix + args, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return
    raise ValueError(f'tailscale serve {action} failed; the panel is not reachable on the tailnet')


def ensure_panel():
    allowed = [x.strip() for x in os.environ.get('AGENT_CRED_ALLOWED_LOGIN', '').split(',') if x.strip()]
    if not allowed:
        raise ValueError('Set AGENT_CRED_ALLOWED_LOGIN to the exact Tailscale login(s) allowed to submit credentials')
    info = panel_alive()
    if info:
        tailscale_serve('on')
        return info
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'HOME', 'USER', 'LANG', 'AGENT_CRED_DIR', 'AGENT_CRED_PANEL_PORT', 'AGENT_CRED_TAILSCALE',
        'AGENT_CRED_TS_PORT', 'AGENT_CRED_ALLOWED_LOGIN', 'AGENT_CRED_IDLE_SECONDS')}
    log = (cred_dir() / 'panel.log').open('ab')
    subprocess.Popen([sys.executable, str(SCRIPTS / 'lab_cred_panel.py'), 'serve'], env=env, stdin=subprocess.DEVNULL,
                     stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        info = panel_alive()
        if info:
            tailscale_serve('on')
            return info
        time.sleep(0.1)
    raise ValueError('The credential panel did not start')


def cmd_request(args):
    template = load_template(args.integration, args.env, args.allow)
    name = args.name or (args.integration if args.integration != 'custom' else None)
    if not name:
        raise ValueError('custom integrations need --name')
    ensure_panel()
    rid, nonce = new_request(name, template, args.purpose)
    print(f'CRED_REQUEST={rid}')
    print('CRED_STATE=PENDING')
    print(f'CRED_EXPIRES_IN={REQUEST_TTL}')
    print(f'CRED_URL={public_base()}/r/{rid}/{nonce}')
    return 0


def credential_state(name):
    record = load_credential(name)
    if record:
        return 'READY', record
    pending = [m for m in (read_json(p) for p in (cred_dir() / 'requests').glob('*.json'))
               if m and m['name'] == name and m['state'] == 'pending' and now() <= m['expires']]
    return ('PENDING' if pending else 'ABSENT'), None


def cmd_status(args):
    state, record = credential_state(args.name)
    print(f'CRED_STATE={state}')
    if record:
        left = 'session' if record['expires'] is None else int(record['expires'] - now())
        print(f'CRED_ENV={record["env"]}')
        print(f'CRED_EXPIRES={left}')
    return 0 if state == 'READY' else 1


def cmd_list(_args):
    for path in sorted((cred_dir() / 'store').glob('*.json')):
        record = load_credential(path.stem)
        if record:
            left = 'session' if record['expires'] is None else f"{int(record['expires'] - now())}s"
            print(f'{record["name"]}\t{record["integration"]}\t{record["env"]}\t{left}')
    return 0


def _parse_jsonc(source):
    out = []
    i = 0
    in_string = False
    escaped = False
    while i < len(source):
        ch = source[i]
        if in_string:
            out.append(ch)
            if escaped: escaped = False
            elif ch == '\\': escaped = True
            elif ch == '"': in_string = False
            i += 1
        elif ch == '"':
            in_string = True; out.append(ch); i += 1
        elif source.startswith('//', i):
            end = source.find('\n', i + 2)
            if end < 0: break
            out.append('\n'); i = end + 1
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0: raise ValueError('Unterminated JSONC comment')
            out.extend('\n' for ch in source[i:end + 2] if ch == '\n')
            i = end + 2
        else:
            out.append(ch); i += 1
    cleaned = ''.join(out)
    out = []
    i = 0; in_string = False; escaped = False
    while i < len(cleaned):
        ch = cleaned[i]
        if in_string:
            out.append(ch)
            if escaped: escaped = False
            elif ch == '\\': escaped = True
            elif ch == '"': in_string = False
            i += 1
        elif ch == '"':
            in_string = True; out.append(ch); i += 1
        elif ch == ',':
            j = i + 1
            while j < len(cleaned) and cleaned[j].isspace(): j += 1
            if j < len(cleaned) and cleaned[j] in '}]': i += 1
            else: out.append(ch); i += 1
        else:
            out.append(ch); i += 1
    return json.loads(''.join(out))


def _cloudflare_config(cwd, command):
    """Reject project-controlled config execution and build hooks while a token is present."""
    import tomllib
    cwd = Path(cwd).resolve()
    args = command[1:]
    # Wrangler can redirect deploys through .wrangler/deploy/config.json and may
    # discover configs above cwd. Neither may silently select unreviewed build hooks.
    for directory in (cwd, *cwd.parents):
        if (directory / '.wrangler/deploy/config.json').is_file():
            raise ValueError('Credentialed Wrangler deploy refuses .wrangler/deploy/config.json redirects')
        if directory != cwd and any((directory / name).is_file() for name in
                                    ('wrangler.toml', 'wrangler.json', 'wrangler.jsonc', 'wrangler.js',
                                     'wrangler.cjs', 'wrangler.mjs', 'wrangler.ts')):
            raise ValueError('Credentialed Wrangler deploy refuses ancestor Wrangler configs; run from the project root')
    config_path = None
    for i, arg in enumerate(args):
        if arg == '--config' and i + 1 < len(args):
            config_path = Path(args[i + 1])
        elif arg.startswith('--config='):
            config_path = Path(arg.split('=', 1)[1])
    if config_path is not None:
        config_path = (cwd / config_path).resolve() if not config_path.is_absolute() else config_path.resolve()
        if not config_path.is_relative_to(cwd) or config_path.suffix.lower() not in ('.toml', '.json', '.jsonc'):
            raise ValueError('Credentialed Wrangler runs require a project-local TOML, JSON, or JSONC config')
    else:
        candidates = [cwd / name for name in ('wrangler.toml', 'wrangler.json', 'wrangler.jsonc')
                      if (cwd / name).is_file()]
        if len(candidates) > 1:
            raise ValueError('Credentialed Wrangler deploy refuses ambiguous config files; pass --config explicitly')
        if candidates:
            config_path = candidates[0]
        elif any((cwd / name).exists() for name in ('wrangler.json5', 'wrangler.js', 'wrangler.cjs', 'wrangler.mjs', 'wrangler.ts')):
            raise ValueError('Credentialed Wrangler runs refuse executable config; use TOML, JSON, or JSONC')
    if not config_path or not config_path.is_file():
        raise ValueError('Credentialed Wrangler deploy requires an existing project-local Wrangler config')
    try:
        data = (tomllib.loads(config_path.read_text()) if config_path.suffix.lower() == '.toml'
                else _parse_jsonc(config_path.read_text()) if config_path.suffix.lower() == '.jsonc'
                else json.loads(config_path.read_text()))
    except (OSError, ValueError) as exc:
        raise ValueError('Wrangler config could not be parsed safely') from exc
    build = data.get('build', {}) if isinstance(data, dict) else {}
    if isinstance(build, dict) and build.get('command'):
        raise ValueError('Credentialed Wrangler deploy refuses config build.command hooks; build first without credentials')


def _credential_environment(record):
    # Never forward ambient secrets or unrelated credentials to the child process.
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_NAMES}
    env['PATH'] = SAFE_PATH
    env.setdefault('HOME', str(Path.home()))
    env[record['env']] = record['value']
    return env


def _redact_output(data, secret):
    if not data:
        return data
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    if not secret_bytes:
        return data
    return data.replace(secret_bytes, b'[REDACTED CREDENTIAL]')


def cmd_run(args):
    command = list(args.command)
    if command and command[0] == '--': command.pop(0)
    if not command: raise ValueError('Provide a command after --')
    if os.environ.get('AGENT_JOB_ID'):
        raise ValueError('Credentials are not available inside managed jobs')
    record = load_credential(args.name)
    if not record: raise ValueError(f'No active credential named {args.name}; run: agent-lab cred request ...')
    requested = command[0]
    if Path(requested).name != requested or '/' in requested or '\\' in requested:
        raise ValueError('Use the exact allowed executable name, not a path or wrapper')
    if requested == 'wrangler' and (record.get('integration') != 'cloudflare' or record.get('env') != 'CLOUDFLARE_API_TOKEN'):
        raise ValueError('Wrangler credentials must use the Cloudflare integration policy')
    if requested not in record['allowed_commands']:
        raise ValueError(f'{requested} is not an allowed command for {args.name} (allowed: {", ".join(record["allowed_commands"])})')
    resolved = shutil.which(requested, path=SAFE_PATH)
    if not resolved:
        raise ValueError(f'Command not found: {requested}')
    executable = Path(resolved).resolve()
    if executable.is_relative_to(Path.cwd().resolve()):
        raise ValueError('Refusing an executable located inside the current project; use a trusted installed executable')
    if record.get('integration') == 'cloudflare':
        if len(command) < 2 or command[1] != 'deploy':
            raise ValueError('Cloudflare credentials are restricted to `wrangler deploy`')
        _cloudflare_config(Path.cwd(), command)
    secret = record['value']
    env = _credential_environment(record)
    # Capture and redact both streams: child tools sometimes echo request headers or env.
    # A bounded output limit prevents accidental unbounded memory use from the child.
    process = subprocess.Popen([str(executable), *command[1:]], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    limit = 1024 * 1024
    captured = {process.stdout: bytearray(), process.stderr: bytearray()}
    truncated = {process.stdout: False, process.stderr: False}
    selector = selectors.DefaultSelector()
    for stream in captured:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + int(os.environ.get('AGENT_CRED_COMMAND_TIMEOUT', '1800'))
    timed_out = False
    while selector.get_map():
        if time.monotonic() >= deadline and not timed_out:
            timed_out = True
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        for key, _ in selector.select(0.1):
            stream = key.fileobj
            try: chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError: continue
            if not chunk:
                selector.unregister(stream); stream.close(); continue
            room = limit - len(captured[stream])
            if room > 0: captured[stream].extend(chunk[:room])
            if len(chunk) > room: truncated[stream] = True
        if timed_out and process.poll() is not None and time.monotonic() > deadline + 2:
            for stream in list(selector.get_map().values()):
                selector.unregister(stream.fileobj); stream.fileobj.close()
            break
    process.wait()
    selector.close()
    outputs = []
    for stream in (process.stdout, process.stderr):
        data = bytes(captured[stream])
        if truncated[stream]:
            # Do not leave a partial token at a truncation boundary.
            data = data[:max(0, len(data) - len(secret) + 1)]
        data = _redact_output(data, secret).decode('utf-8', 'replace')
        outputs.append(data)
    sys.stdout.write(outputs[0])
    if truncated[process.stdout]: sys.stdout.write('\n... stdout truncated at 1 MiB ...\n')
    sys.stderr.write(outputs[1])
    if truncated[process.stderr]: sys.stderr.write('\n... stderr truncated at 1 MiB ...\n')
    if timed_out: print('Credentialed command timed out and was terminated.', file=sys.stderr)
    return 124 if timed_out else process.returncode


def cmd_revoke(args):
    credential_path(args.name).unlink(missing_ok=True)
    print('CRED_STATE=REVOKED')
    print('Also revoke the token at the provider; deleting it here does not invalidate it.')
    return 0


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='agent-lab cred')
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('request'); p.add_argument('integration'); p.add_argument('--name'); p.add_argument('--purpose', default='')
    p.add_argument('--env'); p.add_argument('--allow', action='append', default=[])
    for action in ('status', 'revoke'):
        sub.add_parser(action).add_argument('name')
    sub.add_parser('list')
    p = sub.add_parser('run'); p.add_argument('name'); p.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        return {'request': cmd_request, 'status': cmd_status, 'list': cmd_list, 'run': cmd_run, 'revoke': cmd_revoke}[args.action](args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'agent-lab cred: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
