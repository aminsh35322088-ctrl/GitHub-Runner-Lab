import http.client
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
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read().decode()
    result = (response.status, data, dict(response.getheaders()))
    conn.close()
    return result


def form(**fields):
    return urllib.parse.urlencode(fields), {'Content-Type': 'application/x-www-form-urlencoded'}


class CredentialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cred-test-')
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {'AGENT_CRED_DIR': str(Path(self.temp.name) / 'cred'),
                                               'AGENT_CRED_TAILSCALE': 'off'})
        patcher.start(); self.addCleanup(patcher.stop)
        os.environ.pop('AGENT_JOB_ID', None); os.environ.pop('AGENT_CRED_ALLOWED_LOGIN', None)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), panel.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, name='demo', env='DEMO_API_TOKEN', allow=('python3',)):
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
            self.assertEqual(web(self.port, 'GET', path)[0], 404)
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': 'other@example.com'})[0], 404)
            self.assertEqual(web(self.port, 'GET', path, headers={'Tailscale-User-Login': 'owner@example.com'})[0], 200)

    def test_run_injects_only_into_allowed_command_and_enforces_policy(self):
        _, _, path = self.request()
        self.submit(path)
        code = 'import os,sys;sys.exit(0 if os.environ.get("DEMO_API_TOKEN")==%r else 3)' % TOKEN
        self.assertEqual(cr.main(['run', 'demo', '--', 'python3', '-c', code]), 0)
        self.assertEqual(cr.main(['run', 'demo', '--', 'env']), 2)  # not an allowed command
        with mock.patch.dict(os.environ, {'AGENT_JOB_ID': 'job-1'}):
            self.assertEqual(cr.main(['run', 'demo', '--', 'python3', '-c', 'pass']), 2)  # managed jobs get nothing
        self.assertNotIn('DEMO_API_TOKEN', os.environ)

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
        for env, allow in (('PLAIN_NAME', ['x']), ('MY_TOKEN', []), ('MY_TOKEN', ['bad cmd'])):
            with self.assertRaises(ValueError): cr.load_template('custom', env, allow)
        with self.assertRaises(ValueError): cr.load_template('../etc')


class EndToEndTest(unittest.TestCase):
    def test_cli_request_submit_use_and_revoke(self):
        with tempfile.TemporaryDirectory(prefix='cred-e2e-') as tmp:
            port = free_port()
            env = {**os.environ, 'AGENT_CRED_DIR': str(Path(tmp) / 'cred'), 'AGENT_CRED_TAILSCALE': 'off',
                   'AGENT_CRED_PANEL_PORT': str(port), 'AGENT_CRED_PUBLIC_URL': f'http://127.0.0.1:{port}'}
            cli = [sys.executable, str(SCRIPTS / 'lab_credentials.py')]
            run = lambda *a: subprocess.run([*cli, *a], env=env, capture_output=True, text=True, timeout=30)
            try:
                out = run('request', 'custom', '--name', 'e2e', '--env', 'E2E_API_TOKEN', '--allow', 'python3', '--purpose', 'e2e')
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
                used = run('run', 'e2e', '--', 'python3', '-c', 'import os,sys;sys.exit(0 if os.environ["E2E_API_TOKEN"]=="%s" else 3)' % TOKEN)
                self.assertEqual(used.returncode, 0, used.stderr); self.assertNotIn(TOKEN, used.stdout + used.stderr)
                self.assertEqual(run('revoke', 'e2e').returncode, 0)
                self.assertEqual(run('status', 'e2e').stdout.strip(), 'CRED_STATE=ABSENT')
            finally:
                info = cr.read_json(Path(tmp) / 'cred' / 'panel.json')
                if info:
                    try: os.kill(info['pid'], signal.SIGTERM)
                    except ProcessLookupError: pass


if __name__ == '__main__':
    unittest.main()
