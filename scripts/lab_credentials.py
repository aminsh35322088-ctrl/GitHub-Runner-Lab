#!/usr/bin/env python3
"""On-demand credentials for trusted foreground commands.

A model asks for a credential with `request`; the user submits it through a one-time,
tailnet-only web page; the model then uses it with `run NAME -- COMMAND`. Values never
appear in output, argv, logs or job environments. Session credentials live only on tmpfs.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lab_common import SCRIPTS, atomic

NAME = re.compile(r'^[a-z][a-z0-9-]{1,40}$')
ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,127}$')
CRED_MARKER = re.compile(r'(^|_)(TOKEN|SECRET|PASSWORD|PASS|KEY|APIKEY|CREDENTIAL|AUTH|BEARER|JWT)(_|$)|(TOKEN|SECRET|APIKEY)$')
COMMAND_NAME = re.compile(r'^[A-Za-z0-9._+-]{1,64}$')
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
        base = Path(configured)
    else:
        runtime = Path('/dev/shm') if Path('/dev/shm').is_dir() else Path(tempfile.gettempdir())
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
        if not allow or not all(COMMAND_NAME.fullmatch(c) for c in allow):
            raise ValueError('custom integrations need at least one --allow COMMAND (plain command names)')
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


def new_request(name, template, purpose=''):
    if not NAME.fullmatch(name):
        raise ValueError('Credential name must be lowercase letters, digits and dashes')
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
    except (ProcessLookupError, PermissionError):
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
    info = panel_alive()
    if info:
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
    rid, nonce = new_request(name, template, args.purpose)
    ensure_panel()
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


def cmd_run(args):
    command = list(args.command)
    if command and command[0] == '--':
        command.pop(0)
    if not command:
        raise ValueError('Provide a command after --')
    if os.environ.get('AGENT_JOB_ID'):
        raise ValueError('Credentials are not available inside managed jobs')
    record = load_credential(args.name)
    if not record:
        raise ValueError(f'No active credential named {args.name}; run: agent-lab cred request ...')
    invoked = Path(command[0]).name
    if invoked not in record['allowed_commands']:
        raise ValueError(f'{invoked} is not an allowed command for {args.name} (allowed: {", ".join(record["allowed_commands"])})')
    if shutil.which(command[0]) is None:
        raise ValueError(f'Command not found: {command[0]}')
    env = os.environ.copy()
    env[record['env']] = record['value']
    record['value'] = None
    return subprocess.run(command, env=env, check=False).returncode


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
