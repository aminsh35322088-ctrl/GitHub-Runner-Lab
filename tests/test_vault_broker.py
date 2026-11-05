from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts import vault_broker as broker


OIDC_VALUE = 'oidc-request-token-that-is-never-logged-1234567890'
SESSION_VALUE = 'runner-session-secret-that-is-never-returned-1234567890'
PROVIDER_VALUE = 'provider-secret-that-must-not-appear-in-metadata'


class FakeVaultHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _json(self, status, body):
        raw = json.dumps(body, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith('/oidc'):
            self.server.oidc_calls += 1
            self.server.last_oidc_auth = self.headers.get('Authorization')
            self._json(200, {'value': 'github-oidc-value-12345678901234567890'})
        elif self.path == '/api/runner/registry':
            self.server.registry_calls += 1
            if self.server.reject_registry_once:
                self.server.reject_registry_once = False
                self._json(401, {'error': 'unauthorized'})
                return
            self._json(200, {'credentials': [{
                'id': 'cred_1', 'name': 'CF_TOKEN', 'service': 'cloudflare',
                'purpose': 'deploy', 'lifetime': '90d', 'delivery': 'proxy',
                'validation': 'active', 'expiresAt': None, 'externallyManaged': False,
                'value': PROVIDER_VALUE, 'value_encrypted': 'ciphertext',
            }]})
        elif self.path == '/api/runner/integrations':
            self._json(200, {'integrations': [{
                'id': 'integration_1', 'name': 'CF_TOKEN', 'service': 'cloudflare',
                'externallyManaged': False, 'value': PROVIDER_VALUE,
                'adapter': {
                    'apiBaseUrl': 'https://api.cloudflare.com/client/v4',
                    'defaultHeaders': {'Accept': 'application/json', 'Authorization': 'Bearer should-not-leak'},
                    'cliTools': [{'id': 'wrangler', 'label': 'Wrangler', 'integration': 'base-url-env',
                                  'variable': 'CLOUDFLARE_API_BASE_URL', 'binary': 'wrangler',
                                  'token': PROVIDER_VALUE}],
                    'privateKey': PROVIDER_VALUE,
                },
            }]})
        else:
            self._json(404, {'error': 'not_found'})

    def do_POST(self):
        length = int(self.headers.get('content-length', '0'))
        body = self.rfile.read(length) if length else b''
        if self.path == '/api/runner/session':
            self.server.session_calls += 1
            self.server.last_session_auth = self.headers.get('Authorization')
            if self.server.reject_session:
                self._json(401, {'error': 'unauthorized'})
                return
            self._json(200, {'token': SESSION_VALUE,
                             'expiresAt': int(time.time() * 1000) + 30 * 60 * 1000})
        elif self.path == '/api/runner/registrations':
            self.server.registration_body = json.loads(body)
            self._json(200, {'link': 'https://vault.example/vault/register/one-time-token',
                             'expiresAt': int(time.time() * 1000) + 600_000})
        elif self.path == '/api/runner/use':
            encoded = self.headers.get('X-Vault-Proxy-Metadata', '')
            decoded = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
            self.server.proxy_metadata = json.loads(decoded)
            self.server.proxy_body = body
            self._json(200, {'status': 200, 'headers': {'content-type': 'application/json'},
                             'body': '{"success":true}', 'truncated': False,
                             'redactions': 0, 'host': 'api.cloudflare.com', 'path': '/user'})
        else:
            self._json(404, {'error': 'not_found'})


class VaultBrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), FakeVaultHandler)
        cls.http.oidc_calls = 0
        cls.http.session_calls = 0
        cls.http.registry_calls = 0
        cls.http.reject_session = False
        cls.http.reject_registry_once = False
        cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.http_thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.http_thread.join(timeout=2)

    def setUp(self):
        self.http = self.__class__.http
        self.http.oidc_calls = 0
        self.http.session_calls = 0
        self.http.registry_calls = 0
        self.http.reject_session = False
        self.http.reject_registry_once = False
        self.http.registration_body = None
        self.http.proxy_metadata = None
        self.http.proxy_body = None
        self.http.last_oidc_auth = None
        self.http.last_session_auth = None
        self.client = broker.VaultClient(
            self.base,
            self.base + '/oidc',
            OIDC_VALUE,
            allow_local_http=True,
        )

    def test_only_fixed_operations_exist_and_status_never_returns_session(self):
        result = self.client.dispatch('status', {})
        self.assertTrue(result['connected'])
        self.assertIn('sessionExpiresAt', result)
        encoded = json.dumps(result)
        self.assertNotIn(SESSION_VALUE, encoded)
        self.assertNotIn(OIDC_VALUE, encoded)
        self.assertEqual(self.http.session_calls, 1)
        with self.assertRaises(broker.BrokerError) as raised:
            self.client.dispatch('session.read', {})
        self.assertEqual(raised.exception.code, 'OPERATION_NOT_ALLOWED')

    def test_registry_is_metadata_only_and_sessions_are_reused_in_broker_memory(self):
        first = self.client.dispatch('tokens.list', {})
        second = self.client.dispatch('tokens.list', {})
        self.assertEqual(self.http.oidc_calls, 1)
        self.assertEqual(self.http.session_calls, 1)
        self.assertEqual(self.http.registry_calls, 2)
        self.assertEqual(first['credentials'][0]['name'], 'CF_TOKEN')
        encoded = json.dumps(first)
        self.assertNotIn(PROVIDER_VALUE, encoded)
        self.assertNotIn('value_encrypted', encoded)
        self.assertNotIn(SESSION_VALUE, encoded)
        self.assertEqual(first, second)

    def test_session_renews_inside_broker_without_exposing_requester_or_session(self):
        self.client.dispatch('tokens.list', {})
        self.client._session_expires_at_ms = int(time.time() * 1000) - 1
        self.client.dispatch('tokens.list', {})
        self.assertEqual(self.http.oidc_calls, 2)
        self.assertEqual(self.http.session_calls, 2)
        self.assertEqual(self.http.last_oidc_auth, 'Bearer ' + OIDC_VALUE)
        self.assertEqual(self.http.last_session_auth, 'Bearer github-oidc-value-12345678901234567890')

    def test_expired_api_session_is_reissued_once_and_safe_operation_is_retried(self):
        self.http.reject_registry_once = True
        result = self.client.dispatch('tokens.list', {})
        self.assertEqual(len(result['credentials']), 1)
        self.assertEqual(self.http.registry_calls, 2)
        self.assertEqual(self.http.oidc_calls, 2)
        self.assertEqual(self.http.session_calls, 2)

    def test_integrations_are_reduced_to_the_approved_cli_contract(self):
        result = self.client.dispatch('integrations.list', {})
        encoded = json.dumps(result)
        self.assertIn('CLOUDFLARE_API_BASE_URL', encoded)
        self.assertIn('Wrangler', encoded)
        self.assertNotIn(PROVIDER_VALUE, encoded)
        self.assertNotIn('privateKey', encoded)
        self.assertNotIn('token"', encoded)
        self.assertNotIn('Authorization', encoded)

    def test_registration_creation_is_schema_limited(self):
        result = self.client.dispatch('registrations.create', {'payload': {
            'name': 'MY_TOKEN', 'service': 'service', 'purpose': 'deploy',
            'doc': {'overview': 'purpose', 'creation_steps': ['step']},
        }})
        self.assertIn('/vault/register/', result['link'])
        self.assertEqual(self.http.registration_body['name'], 'MY_TOKEN')
        with self.assertRaises(broker.BrokerError):
            self.client.dispatch('registrations.create', {'payload': {
                'name': 'MY_TOKEN', 'service': 'service', 'doc': {}, 'secret': PROVIDER_VALUE,
            }})

    def test_credential_use_is_proxy_only_and_validates_headers_and_body(self):
        body = b'{"action":"verify"}'
        result = self.client.dispatch('credential.use', {'payload': {
            'name': 'CF_TOKEN', 'method': 'GET', 'url': 'https://api.cloudflare.com/client/v4/user',
            'purpose': 'read only', 'headers': {'Accept': 'application/json'},
            'body': base64.b64encode(body).decode(),
        }})
        self.assertEqual(result['status'], 200)
        self.assertEqual(self.http.proxy_body, body)
        self.assertEqual(self.http.proxy_metadata['url'], 'https://api.cloudflare.com/client/v4/user')
        self.assertNotIn(SESSION_VALUE, json.dumps(result))
        with self.assertRaises(broker.BrokerError):
            self.client.dispatch('credential.use', {'payload': {
                'name': 'CF_TOKEN', 'method': 'CONNECT', 'url': 'https://api.cloudflare.com/user',
            }})
        with self.assertRaises(broker.BrokerError):
            self.client.dispatch('credential.use', {'payload': {
                'name': 'CF_TOKEN', 'method': 'GET', 'url': 'https://api.cloudflare.com/user',
                'headers': {'Authorization': 'Bearer attacker'},
            }})

    def test_oidc_and_vault_redirects_fail_closed(self):
        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                self.send_response(302); self.send_header('Location', 'http://127.0.0.1/other'); self.end_headers()
        srv = ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            with self.assertRaises(broker.BrokerError) as raised:
                broker._http_request(f'http://127.0.0.1:{srv.server_port}/', 'GET', {}, None, allow_local_http=True)
            self.assertEqual(raised.exception.code, 'UPSTREAM_REDIRECT')
        finally:
            srv.shutdown(); srv.server_close(); t.join(timeout=2)

    def test_unexpected_session_failure_never_returns_secret_details(self):
        self.http.reject_session = True
        with self.assertRaises(broker.BrokerError) as raised:
            self.client.dispatch('tokens.list', {})
        self.assertEqual(raised.exception.code, 'SESSION_REJECTED')
        self.assertNotIn(SESSION_VALUE, str(raised.exception))
        self.assertNotIn(OIDC_VALUE, str(raised.exception))

    def test_unix_socket_checks_peer_uid_and_rejects_session_read(self):
        with tempfile.TemporaryDirectory(prefix='vault-broker-test-') as tmp:
            socket_path = str(Path(tmp) / 'broker.sock')
            server = broker.ThreadedUnixServer(socket_path, self.client, os.getuid())
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                request = {'version': 1, 'id': 'a' * 32, 'operation': 'tokens.list'}
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(3); peer.connect(socket_path)
                    peer.sendall(json.dumps(request).encode() + b'\n'); peer.shutdown(socket.SHUT_WR)
                    response = json.loads(peer.makefile('rb').readline())
                self.assertTrue(response['ok'])
                self.assertEqual(response['id'], request['id'])
                bad = {'version': 1, 'id': 'b' * 32, 'operation': 'session.read'}
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(3); peer.connect(socket_path)
                    peer.sendall(json.dumps(bad).encode() + b'\n'); peer.shutdown(socket.SHUT_WR)
                    response = json.loads(peer.makefile('rb').readline())
                self.assertFalse(response['ok'])
                self.assertEqual(response['error']['code'], 'OPERATION_NOT_ALLOWED')
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_wrong_uid_peer_is_denied_before_any_operation(self):
        with tempfile.TemporaryDirectory(prefix='vault-broker-peer-test-') as tmp:
            socket_path = str(Path(tmp) / 'broker.sock')
            server = broker.ThreadedUnixServer(socket_path, self.client, os.getuid() + 1)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                request = {'version': 1, 'id': 'c' * 32, 'operation': 'status'}
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(3); peer.connect(socket_path)
                    peer.sendall(json.dumps(request).encode() + b'\n'); peer.shutdown(socket.SHUT_WR)
                    response = json.loads(peer.makefile('rb').readline())
                self.assertFalse(response['ok'])
                self.assertEqual(response['error']['code'], 'PEER_DENIED')
                self.assertEqual(self.http.session_calls, 0)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_invalid_frame_version_and_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='vault-broker-frame-test-') as tmp:
            socket_path = str(Path(tmp) / 'broker.sock')
            server = broker.ThreadedUnixServer(socket_path, self.client, os.getuid())
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                for request, expected in [
                    ({'version': 99, 'id': 'd' * 32, 'operation': 'status'}, 'PROTOCOL_MISMATCH'),
                    ({'version': 1, 'id': 'e' * 32, 'operation': 'status', 'extra': 'no'}, 'INVALID_REQUEST'),
                ]:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                        peer.settimeout(3); peer.connect(socket_path)
                        peer.sendall(json.dumps(request).encode() + b'\n'); peer.shutdown(socket.SHUT_WR)
                        response = json.loads(peer.makefile('rb').readline())
                    self.assertFalse(response['ok'])
                    self.assertEqual(response['error']['code'], expected)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
