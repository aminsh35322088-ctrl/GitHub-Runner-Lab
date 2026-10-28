import contextlib
import concurrent.futures
import http.client
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import lab_cred_panel as panel  # noqa: E402
import lab_credentials as cr  # noqa: E402

TOKEN = 'tok_' + 'Z' * 28


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def web(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    request_headers = {'Tailscale-User-Login': 'owner@example.com'}
    request_headers.update(headers or {})
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    data = response.read().decode()
    result = (response.status, data, dict(response.getheaders()))
    conn.close()
    return result


def form(**fields):
    return urllib.parse.urlencode(fields), {'Content-Type': 'application/x-www-form-urlencoded'}


class CredentialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cred-test-', dir='/dev/shm')
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {'AGENT_CRED_DIR': str(Path(self.temp.name) / 'cred'),
                                               'AGENT_CRED_TAILSCALE': 'off', 'AGENT_CRED_ALLOWED_LOGIN': 'owner@example.com'})
        patcher.start(); self.addCleanup(patcher.stop)
        os.environ.pop('AGENT_JOB_ID', None)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), panel.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, name='demo', env='DEMO_API_TOKEN', allow=('cred-probe',)):
        template = cr.load_template('custom', env, list(allow))
        rid, nonce = cr.new_request(name, template, 'unit test')
        return rid, nonce, f'/r/{rid}/{nonce}'

    def submit(self, path, value=TOKEN, lifetime='session'):
        status, body, _ = web(self.port, 'GET', path)
        self.assertEqual(status, 200, body)
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        data, headers = form(csrf=csrf, value=value, lifetime=lifetime)
        return web(self.port, 'POST', path, data, headers)

    def test_submit_stores_value_once_and_never_returns_it(self):
        rid, nonce, path = self.request()
        status, body, headers = self.submit(path, lifetime='1h')
        self.assertEqual(status, 200); self.assertNotIn(TOKEN, body)
        self.assertEqual(cr.load_credential('demo')['value'], TOKEN)
        self.assertEqual(web(self.port, 'GET', path)[0], 410)  # link is burned
        self.assertEqual(web(self.port, 'GET', f'/r/{rid}/{"x" * 43}')[0], 410)
        meta_text = (Path(os.environ['AGENT_CRED_DIR']) / 'requests' / f'{rid}.json').read_text()
        self.assertNotIn(TOKEN, meta_text); self.assertNotIn(nonce, meta_text)
        for path_ in Path(os.environ['AGENT_CRED_DIR']).rglob('*.json'):
            self.assertEqual(path_.stat().st_mode & 0o777, 0o600, path_)

    def test_responses_are_not_cacheable_and_have_csp(self):
        _, _, path = self.request()
        status, body, headers = web(self.port, 'GET', path)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')

    def test_wrong_key_unknown_route_and_missing_csrf_are_rejected(self):
        rid, nonce, path = self.request()
        self.assertEqual(web(self.port, 'GET', f'/r/{rid}/{"A" * 43}')[0], 410)
        self.assertEqual(web(self.port, 'GET', '/')[0], 404)
        web(self.port, 'GET', path)
        data, headers = form(csrf='wrong', value=TOKEN, lifetime='session')
        self.assertEqual(web(self.port, 'POST', path, data, headers)[0], 400)
        self.assertIsNone(cr.load_credential('demo'))

    def test_attempts_are_limited_and_bad_input_keeps_request_pending(self):
        rid, nonce, path = self.request()
        status, body, _ = self.submit(path, value='short')
        self.assertEqual(status, 400); self.assertIsNone(cr.load_credential('demo'))
        for _ in range(panel.MAX_ATTEMPTS):
            status, _, _ = self.submit(path, value='x') if cr.load_request(rid)['state'] == 'pending' else (429, '', {})
        self.assertEqual(cr.load_request(rid)['state'], 'locked')
        self.assertEqual(web(self.port, 'GET', path)[0], 410)

    def test_provider_validation_failure_keeps_request_pending(self):
        rid, nonce, path = self.request()
        with mock.patch.object(cr, 'validate_value', return_value=(False, 'The provider did not accept this token.')):
            status, body, _ = self.submit(path)
        self.assertEqual(status, 400); self.assertIn('did not accept', body)
        self.assertIsNone(cr.load_credential('demo')); self.assertEqual(cr.load_request(rid)['state'], 'pending')

    def test_expired_request_is_refused(self):
        rid, nonce, path = self.request()
        meta = cr.load_request(rid); meta['expires'] = time.time() - 1; cr.save_request(meta)
        self.assertEqual(web(self.port, 'GET', path)[0], 410)

    def test_tailscale_identity_allowlist(self):
        _, _, path = self.request()
        with mock.patch.dict(os.environ, {'AGENT_CRED_ALLOWED_LOGIN': 'owner@example.com'}):
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': ''})[0], 404)
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': 'other@example.com'})[0], 404)
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': 'owner@example.com'})[0], 200)
        with mock.patch.dict(os.environ, {'AGENT_CRED_ALLOWED_LOGIN': ''}):
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': 'owner@example.com'})[0], 404)

    def make_probe(self):
        bindir = Path(self.temp.name) / 'bin'; bindir.mkdir(exist_ok=True)
        probe = bindir / 'cred-probe'
        probe.write_text('#!/usr/bin/python3\nimport os\nprint(os.environ.get("DEMO_API_TOKEN", "MISSING"))\nprint(os.environ.get("AWS_SECRET_ACCESS_KEY", "AMBIENT_SECRET_NOT_INHERITED"))\n')
        probe.chmod(0o755)
        return probe

    def test_run_redacts_output_scrubs_ambient_secrets_and_enforces_policy(self):
        self.make_probe()
        _, _, path = self.request()
        self.submit(path)
        with mock.patch.dict(os.environ, {'AWS_SECRET_ACCESS_KEY': 'ambient-secret-value'}):
            stdout = io.StringIO()
            with mock.patch.object(cr.shutil, 'which', return_value=str(self.make_probe())):
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(cr.main(['run', 'demo', '--', 'cred-probe']), 0)
        self.assertNotIn(TOKEN, stdout.getvalue())
        self.assertIn('[REDACTED CREDENTIAL]', stdout.getvalue())
        self.assertIn('AMBIENT_SECRET_NOT_INHERITED', stdout.getvalue())
        self.assertEqual(cr.main(['run', 'demo', '--', 'env']), 2)  # not an allowed command
        with mock.patch.dict(os.environ, {'AGENT_JOB_ID': 'job-1'}):
            with mock.patch.object(cr.shutil, 'which', return_value=str(self.make_probe())):
                self.assertEqual(cr.main(['run', 'demo', '--', 'cred-probe']), 2)  # managed jobs get nothing
        self.assertNotIn('DEMO_API_TOKEN', os.environ)

    def test_credentialed_command_timeout_kills_process_group(self):
        probe = Path(self.temp.name) / 'bin' / 'cred-probe'
        probe.parent.mkdir(exist_ok=True)
        probe.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(10)\n')
        probe.chmod(0o755)
        _, _, path = self.request(); self.submit(path)
        with mock.patch.object(cr.shutil, 'which', return_value=str(probe)), \
             mock.patch.dict(os.environ, {'AGENT_CRED_COMMAND_TIMEOUT': '1'}):
            started = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cr.main(['run', 'demo', '--', 'cred-probe']), 124)
            self.assertLess(time.monotonic() - started, 5)

    def test_run_rejects_path_based_same_name_executable(self):
        probe = self.make_probe()
        _, _, path = self.request(); self.submit(path)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cr.main(['run', 'demo', '--', str(probe)]), 2)

    def test_cloudflare_deploy_rejects_config_redirects_ancestor_and_ambiguous_configs(self):
        cwd = Path(self.temp.name) / 'project'; cwd.mkdir()
        (cwd / 'wrangler.toml').write_text('name = "example"\n')
        (cwd / 'wrangler.jsonc').write_text('{"name":"example"}')
        with self.assertRaisesRegex(ValueError, 'ambiguous config'):
            cr._cloudflare_config(cwd, ['wrangler', 'deploy'])
        (cwd / 'wrangler.jsonc').unlink()
        redirect = cwd / '.wrangler' / 'deploy'; redirect.mkdir(parents=True)
        (redirect / 'config.json').write_text('{"configPath":"../../wrangler.toml"}')
        with self.assertRaisesRegex(ValueError, 'redirects'):
            cr._cloudflare_config(cwd, ['wrangler', 'deploy'])
        (redirect / 'config.json').unlink()
        parent_config = cwd.parent / 'wrangler.json'
        parent_config.write_text('{"name":"shadow"}')
        try:
            with self.assertRaisesRegex(ValueError, 'ancestor Wrangler configs'):
                cr._cloudflare_config(cwd, ['wrangler', 'deploy'])
        finally:
            parent_config.unlink(missing_ok=True)

    def test_custom_integration_cannot_bypass_cloudflare_wrangler_policy(self):
        with self.assertRaisesRegex(ValueError, 'safe, plain executable'):
            cr.load_template('custom', 'CLOUDFLARE_API_TOKEN', ['wrangler'])

    def test_public_url_must_be_https_tailscale_origin(self):
        with mock.patch.dict(os.environ, {'AGENT_CRED_PUBLIC_URL': 'http://attacker.example'}):
            with self.assertRaisesRegex(ValueError, 'HTTPS Tailscale'):
                cr.public_base()
        with mock.patch.dict(os.environ, {'AGENT_CRED_PUBLIC_URL': 'https://github-lab.example.ts.net:8443'}):
            self.assertEqual(cr.public_base(), 'https://github-lab.example.ts.net:8443')

    def test_non_tmpfs_credential_directory_fails_closed(self):
        with mock.patch.dict(os.environ, {'AGENT_CRED_DIR': '/tmp/agent-cred-disk-test', 'AGENT_CRED_ALLOW_NON_TMPFS': '1'}):
            with self.assertRaisesRegex(ValueError, 'must be on /dev/shm tmpfs'):
                cr.cred_dir()

    def test_cloudflare_deploy_rejects_project_build_hooks_and_executable_config(self):
        cwd = Path(self.temp.name) / 'project'; cwd.mkdir()
        config = cwd / 'wrangler.toml'
        config.write_text('name = "example"\n[build]\ncommand = "echo leak"\n')
        with self.assertRaisesRegex(ValueError, 'build.command'):
            cr._cloudflare_config(cwd, ['wrangler', 'deploy'])
        config.unlink()
        jsonc = cwd / 'wrangler.jsonc'
        jsonc.write_text('{\n // build hook is forbidden\n "name": "example",\n "build": {"command": "echo leak",},\n}')
        with self.assertRaisesRegex(ValueError, 'build.command'):
            cr._cloudflare_config(cwd, ['wrangler', 'deploy'])
        jsonc.unlink(); (cwd / 'wrangler.ts').write_text('export default {}')
        with self.assertRaisesRegex(ValueError, 'executable config'):
            cr._cloudflare_config(cwd, ['wrangler', 'deploy'])

    def test_duplicate_active_or_pending_credential_names_are_refused(self):
        template = cr.load_template('custom', 'DEMO_API_TOKEN', ['cred-probe'])
        cr.new_request('demo', template, 'first request')
        with self.assertRaisesRegex(ValueError, 'already pending'):
            cr.new_request('demo', template, 'duplicate request')

    def test_parallel_submission_only_fulfills_once(self):
        rid, nonce, path = self.request()
        status, body, _ = web(self.port, 'GET', path)
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        data, headers = form(csrf=csrf, value=TOKEN, lifetime='session')
        def submit_once(): return web(self.port, 'POST', path, data, headers)[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: submit_once(), range(2)))
        self.assertEqual(statuses.count(200), 1, statuses)
        self.assertEqual(cr.load_request(rid)['state'], 'fulfilled')

    def test_expired_credential_is_removed_and_revoke_deletes(self):
        _, _, path = self.request()
        self.submit(path, lifetime='1h')
        record = json.loads(cr.credential_path('demo').read_text()); record['expires'] = time.time() - 1
        cr.credential_path('demo').write_text(json.dumps(record))
        self.assertIsNone(cr.load_credential('demo')); self.assertFalse(cr.credential_path('demo').exists())
        _, _, path = self.request(); self.submit(path)
        self.assertEqual(cr.main(['revoke', 'demo']), 0); self.assertIsNone(cr.load_credential('demo'))

    def test_templates(self):
        template = cr.load_template('cloudflare')
        self.assertEqual(template['env'], 'CLOUDFLARE_API_TOKEN'); self.assertIn('wrangler', template['allowed_commands'])
        self.assertNotIn('npx', template['allowed_commands'])
        for env, allow in (('PLAIN_NAME', ['x']), ('MY_TOKEN', []), ('MY_TOKEN', ['bad cmd']), ('MY_TOKEN', ['python3'])):
            with self.assertRaises(ValueError): cr.load_template('custom', env, allow)
        with self.assertRaises(ValueError): cr.load_template('../etc')


class EndToEndTest(unittest.TestCase):
    def test_cli_request_submit_use_and_revoke(self):
        with tempfile.TemporaryDirectory(prefix='cred-e2e-', dir='/dev/shm') as tmp:
            port = free_port()
            bindir = Path(tmp) / 'bin'; bindir.mkdir()
            probe = bindir / 'cred-probe'
            probe.write_text('#!/usr/bin/python3\nimport os,sys\nsys.exit(0 if os.environ.get(\"E2E_API_TOKEN\") == %r else 3)\n' % TOKEN)
            probe.chmod(0o755)
            env = {**os.environ, 'AGENT_CRED_DIR': str(Path(tmp) / 'cred'), 'AGENT_CRED_TAILSCALE': 'off',
                   'AGENT_CRED_ALLOWED_LOGIN': 'owner@example.com', 'AGENT_CRED_PANEL_PORT': str(port),
                   'AGENT_CRED_PUBLIC_URL': f'https://github-lab.example.ts.net:{port}'}
            cli = [sys.executable, str(SCRIPTS / 'lab_credentials.py')]
            run = lambda *a: subprocess.run([*cli, *a], env=env, capture_output=True, text=True, timeout=30)
            try:
                out = run('request', 'custom', '--name', 'e2e', '--env', 'E2E_API_TOKEN', '--allow', 'printenv', '--purpose', 'e2e')
                self.assertEqual(out.returncode, 0, out.stderr)
                url = re.search(r'CRED_URL=(\S+)', out.stdout)[1]
                self.assertIn('CRED_STATE=PENDING', out.stdout); self.assertEqual(run('status', 'e2e').stdout.strip(), 'CRED_STATE=PENDING')
                path = urllib.parse.urlparse(url).path
                status, body, _ = web(port, 'GET', path)
                csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
                data, headers = form(csrf=csrf, value=TOKEN, lifetime='session')
                self.assertEqual(web(port, 'POST', path, data, headers)[0], 200)
                status_out = run('status', 'e2e')
                self.assertEqual(status_out.returncode, 0); self.assertNotIn(TOKEN, status_out.stdout + run('list').stdout)
                used = run('run', 'e2e', '--', 'printenv', 'E2E_API_TOKEN')
                self.assertEqual(used.returncode, 0, used.stderr); self.assertNotIn(TOKEN, used.stdout + used.stderr)
                self.assertIn('[REDACTED CREDENTIAL]', used.stdout)
                self.assertEqual(run('revoke', 'e2e').returncode, 0)
                self.assertEqual(run('status', 'e2e').stdout.strip(), 'CRED_STATE=ABSENT')
            finally:
                info = cr.read_json(Path(tmp) / 'cred' / 'panel.json')
                if info:
                    try: os.kill(info['pid'], signal.SIGTERM)
                    except ProcessLookupError: pass


if __name__ == '__main__':
    unittest.main()
