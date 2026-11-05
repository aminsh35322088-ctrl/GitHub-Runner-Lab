#!/usr/bin/env python3
"""Restricted local Vault broker. No credential/session read operation exists."""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import ssl
import stat
import struct
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 15 * 1024 * 1024
MAX_HTTP_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024
MAX_JSON_BODY_BYTES = 512 * 1024
SESSION_REFRESH_MARGIN_MS = 120_000
DEFAULT_HTTP_TIMEOUT = 8

SAFE_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
SAFE_REQUEST_HEADERS = frozenset({
    "accept", "accept-language", "content-type", "if-match", "if-none-match",
    "x-github-api-version", "x-api-version", "anthropic-version",
    "anthropic-workspace-id", "x-pinecone-api-version",
})
TOKEN_METADATA_FIELDS = (
    "id", "name", "service", "purpose", "lifetime", "delivery", "validation",
    "expiresAt", "externallyManaged",
)
INTEGRATION_FIELDS = ("id", "name", "service", "externallyManaged", "adapter")
CLI_TOOL_FIELDS = ("id", "label", "integration", "variable", "tokenVariable", "flag", "binary")


class BrokerError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _bounded_text(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BrokerError("INVALID_REQUEST", f"{field} is invalid")
    return value.strip()


def _strict_object(value: object, allowed: set[str], required: set[str], field: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or not required.issubset(value):
        raise BrokerError("INVALID_REQUEST", f"{field} has an invalid shape")
    return value


def _safe_url(value: object, allow_local_http: bool = False) -> str:
    raw = _bounded_text(value, 2048, "url")
    parsed = urlsplit(raw)
    local = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (allow_local_http and parsed.scheme == "http" and local):
        raise BrokerError("INVALID_CONFIG", "Vault origin must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise BrokerError("INVALID_CONFIG", "Vault origin is invalid")
    if parsed.port not in (None, 443) and not (allow_local_http and local):
        raise BrokerError("INVALID_CONFIG", "Vault origin port is not allowed")
    if parsed.path not in ("", "/") or parsed.query:
        raise BrokerError("INVALID_CONFIG", "Vault URL must be an origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _safe_oidc_request_url(value: object, allow_local_http: bool = False) -> str:
    raw = _bounded_text(value, 4096, "OIDC request URL")
    parsed = urlsplit(raw)
    local = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    trusted_host = bool(parsed.hostname and parsed.hostname.lower().endswith(".actions.githubusercontent.com"))
    if not trusted_host and not (allow_local_http and local and parsed.scheme == "http"):
        raise BrokerError("INVALID_CONFIG", "OIDC request host is not trusted")
    if parsed.scheme != "https" and not (allow_local_http and local and parsed.scheme == "http"):
        raise BrokerError("INVALID_CONFIG", "OIDC request URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise BrokerError("INVALID_CONFIG", "OIDC request URL is invalid")
    return raw


def _http_request(url: str, method: str, headers: dict[str, str], body: bytes | None,
                  timeout: float = DEFAULT_HTTP_TIMEOUT, allow_local_http: bool = False) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    local = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                  timeout=timeout, context=ssl.create_default_context())
    elif allow_local_http and parsed.scheme == "http" and local:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    else:
        raise BrokerError("INVALID_CONFIG", "Refusing a non-HTTPS upstream")
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        # Deliberately do not follow redirects. OIDC and Vault origins are fixed.
        if 300 <= response.status < 400:
            raise BrokerError("UPSTREAM_REDIRECT", "Upstream redirect was refused", True)
        payload = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        if len(payload) > MAX_HTTP_RESPONSE_BYTES:
            raise BrokerError("UPSTREAM_RESPONSE_TOO_LARGE", "Upstream response exceeded the limit")
        selected = {k.lower(): v for k, v in response.getheaders()
                    if k.lower() in {"content-type", "retry-after", "x-request-id"}}
        return response.status, selected, payload
    except BrokerError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise BrokerError("UPSTREAM_UNAVAILABLE", "Upstream request failed or timed out", True) from exc
    finally:
        connection.close()


def _decode_json(payload: bytes, code: str = "UPSTREAM_INVALID_RESPONSE") -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerError(code, "Upstream returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise BrokerError(code, "Upstream returned an invalid response shape")
    return value


def _select_fields(value: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in fields if key in value}


def _public_registry(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Credential registry is invalid")
    return [_select_fields(item, TOKEN_METADATA_FIELDS) for item in value if isinstance(item, dict)]


def _public_integrations(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Integration registry is invalid")
    output = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = _select_fields(item, INTEGRATION_FIELDS)
        adapter = item.get("adapter")
        if isinstance(adapter, dict):
            safe_adapter = {k: adapter[k] for k in ("apiBaseUrl",) if k in adapter}
            default_headers = adapter.get("defaultHeaders")
            if isinstance(default_headers, dict):
                safe_adapter["defaultHeaders"] = {
                    str(k).lower(): v for k, v in default_headers.items()
                    if str(k).lower() in SAFE_REQUEST_HEADERS and isinstance(v, str)
                    and len(v) <= 500 and "\r" not in v and "\n" not in v
                }
            tools = adapter.get("cliTools")
            if isinstance(tools, list):
                safe_adapter["cliTools"] = [_select_fields(tool, CLI_TOOL_FIELDS) for tool in tools if isinstance(tool, dict)]
            row["adapter"] = safe_adapter
        output.append(row)
    return output


class VaultClient:
    def __init__(self, vault_url: str, oidc_request_url: str, oidc_request_token: str,
                 allow_local_http: bool = False):
        self.base = _safe_url(vault_url, allow_local_http=allow_local_http)
        self.oidc_url = _safe_oidc_request_url(oidc_request_url, allow_local_http=allow_local_http)
        self._oidc_request_token = _bounded_text(oidc_request_token, 8192, "OIDC requester token")
        self._session_token: str | None = None
        self._session_expires_at_ms = 0
        self._identity: dict = {}
        self._lock = threading.RLock()
        self._allow_local_http = allow_local_http

    def _oidc(self) -> str:
        parsed = urlsplit(self.oidc_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if k != "audience"]
        query.append(("audience", self.base))
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
        status, _, raw = _http_request(url, "GET", {
            "Authorization": "Bearer " + self._oidc_request_token,
            "Accept": "application/json",
        }, None, allow_local_http=self._allow_local_http)
        if status != 200:
            raise BrokerError("OIDC_UNAVAILABLE", "GitHub OIDC token request was refused", status in (429, 500, 502, 503, 504))
        body = _decode_json(raw, "OIDC_INVALID_RESPONSE")
        token = body.get("value")
        if not isinstance(token, str) or not 20 <= len(token) <= 8192:
            raise BrokerError("OIDC_INVALID_RESPONSE", "GitHub OIDC response did not contain a valid token")
        return token

    def _ensure_session(self) -> None:
        now_ms = int(time.time() * 1000)
        if self._session_token and self._session_expires_at_ms - now_ms > SESSION_REFRESH_MARGIN_MS:
            return
        with self._lock:
            now_ms = int(time.time() * 1000)
            if self._session_token and self._session_expires_at_ms - now_ms > SESSION_REFRESH_MARGIN_MS:
                return
            oidc_token = self._oidc()
            status, _, raw = _http_request(self.base + "/api/runner/session", "POST", {
                "Authorization": "Bearer " + oidc_token,
                "Accept": "application/json",
            }, None, allow_local_http=self._allow_local_http)
            # Drop the local reference as soon as the exchange completes.
            oidc_token = ""
            if status != 200:
                code = "SESSION_REJECTED" if status in (401, 403) else "VAULT_UNAVAILABLE"
                raise BrokerError(code, "Vault did not issue a Runner Session", status in (429, 500, 502, 503, 504))
            body = _decode_json(raw)
            token = body.get("token")
            expires_at = body.get("expiresAt")
            if not isinstance(token, str) or not 20 <= len(token) <= 8192:
                raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Vault returned an invalid Runner Session")
            try:
                expires_ms = int(expires_at)
            except (TypeError, ValueError):
                raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Vault returned an invalid Session expiry")
            if expires_ms <= now_ms or expires_ms > now_ms + 60 * 60 * 1000:
                raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Vault returned an out-of-range Session expiry")
            self._session_token = token
            self._session_expires_at_ms = expires_ms
            # No raw claim or JWT data is returned by this broker.
            self._identity = {"available": True}

    def _api(self, method: str, route: str, body: bytes | None = None,
             extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
        # A 401 from the Runner API is an authentication failure before the
        # operation is dispatched, so one fresh OIDC exchange and one retry are
        # safe even for registration/use. No provider-side request was made.
        for attempt in range(2):
            self._ensure_session()
            assert self._session_token is not None
            session_used = self._session_token
            headers = {"Authorization": "Bearer " + session_used, "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if extra_headers:
                headers.update(extra_headers)
            status, _, raw = _http_request(self.base + route, method, headers, body,
                                          allow_local_http=self._allow_local_http)
            try:
                response = _decode_json(raw)
            except BrokerError:
                response = {"error": "invalid_upstream_response"}
            if status == 401 and attempt == 0:
                with self._lock:
                    if self._session_token == session_used:
                        self._session_token = None
                        self._session_expires_at_ms = 0
                continue
            if status == 401:
                raise BrokerError("SESSION_EXPIRED", "Vault Session expired; retry the operation", True)
            if status == 403:
                raise BrokerError("POLICY_DENIED", "Vault policy denied this operation")
            if status == 429:
                raise BrokerError("RATE_LIMITED", "Vault rate limit reached", True)
            if status >= 500:
                raise BrokerError("VAULT_UNAVAILABLE", "Vault service is temporarily unavailable", True)
            if status >= 400:
                raise BrokerError("REQUEST_REJECTED", "Vault rejected the operation")
            return status, response
        raise BrokerError("SESSION_EXPIRED", "Vault Session expired; retry the operation", True)

    def dispatch(self, operation: object, request: dict) -> dict:
        if operation == "status":
            self._ensure_session()
            return {
                "connected": True,
                "sessionExpiresAt": self._session_expires_at_ms,
                "brokerProtocol": PROTOCOL_VERSION,
                "operations": ["tokens.list", "integrations.list", "registrations.create", "credential.use"],
            }
        if operation == "tokens.list":
            _, body = self._api("GET", "/api/runner/registry")
            return {"credentials": _public_registry(body.get("credentials"))}
        if operation == "integrations.list":
            _, body = self._api("GET", "/api/runner/integrations")
            return {"integrations": _public_integrations(body.get("integrations"))}
        if operation == "registrations.create":
            payload = _strict_object(request.get("payload"), {"name", "service", "purpose", "doc"},
                                     {"name", "service", "doc"}, "registration")
            name = _bounded_text(payload.get("name"), 80, "name")
            service = _bounded_text(payload.get("service"), 80, "service")
            purpose = payload.get("purpose")
            if purpose is not None:
                purpose = _bounded_text(purpose, 240, "purpose")
            doc = payload.get("doc")
            if not isinstance(doc, dict) or len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) > MAX_JSON_BODY_BYTES:
                raise BrokerError("INVALID_REQUEST", "registration document is invalid or too large")
            data = {"name": name, "service": service, "doc": doc}
            if purpose is not None:
                data["purpose"] = purpose
            _, body = self._api("POST", "/api/runner/registrations", json.dumps(data, separators=(",", ":")).encode())
            link = _bounded_text(body.get("link"), 4096, "registration link")
            expires_at = body.get("expiresAt")
            if not isinstance(expires_at, (int, float)):
                raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Vault returned an invalid registration expiry")
            return {"link": link, "ownerInbox": self.base + "/vault/requests", "expiresAt": expires_at}
        if operation == "credential.use":
            payload = _strict_object(request.get("payload"),
                {"name", "method", "url", "headers", "purpose", "consumer", "body"},
                {"name", "method", "url"}, "credential use")
            name = _bounded_text(payload.get("name"), 80, "name")
            method = _bounded_text(payload.get("method"), 12, "method").upper()
            if method not in SAFE_METHODS:
                raise BrokerError("INVALID_REQUEST", "HTTP method is not allowed")
            raw_url = _bounded_text(payload.get("url"), 2000, "url")
            # The proxy accepts HTTPS absolute URLs. Validate the scheme/host but
            # preserve path/query, which the Vault policy validates independently.
            parsed = urlsplit(raw_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise BrokerError("INVALID_REQUEST", "credential target must be an HTTPS URL")
            if parsed.port not in (None, 443):
                raise BrokerError("INVALID_REQUEST", "credential target port is not allowed")
            headers_in = payload.get("headers", {})
            if not isinstance(headers_in, dict) or len(headers_in) > 20:
                raise BrokerError("INVALID_REQUEST", "request headers are invalid")
            headers = {}
            for key, value in headers_in.items():
                normalized = str(key).lower()
                if normalized not in SAFE_REQUEST_HEADERS or not isinstance(value, str) or len(value) > 4096 or "\r" in value or "\n" in value:
                    raise BrokerError("INVALID_REQUEST", "request header is not allowed")
                headers[normalized] = value
            purpose = payload.get("purpose")
            if purpose is not None:
                purpose = _bounded_text(purpose, 240, "purpose")
            consumer = payload.get("consumer")
            if consumer is not None and (not isinstance(consumer, str) or not re.fullmatch(r"cli:[a-z0-9._-]{1,40}", consumer, re.I)):
                raise BrokerError("INVALID_REQUEST", "consumer is invalid")
            body_b64 = payload.get("body")
            request_body = b""
            if body_b64 is not None:
                if not isinstance(body_b64, str) or len(body_b64) > ((MAX_REQUEST_BODY_BYTES + 2) // 3) * 4:
                    raise BrokerError("INVALID_REQUEST", "request body is too large")
                try:
                    request_body = base64.b64decode(body_b64, validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    raise BrokerError("INVALID_REQUEST", "request body encoding is invalid") from exc
                if len(request_body) > MAX_REQUEST_BODY_BYTES:
                    raise BrokerError("INVALID_REQUEST", "request body is too large")
            metadata = {"name": name, "method": method, "url": raw_url, "headers": headers}
            if purpose is not None: metadata["purpose"] = purpose
            if consumer is not None: metadata["consumer"] = consumer
            encoded = base64.urlsafe_b64encode(json.dumps(metadata, separators=(",", ":")).encode()).rstrip(b"=").decode("ascii")
            status, body = self._api("POST", "/api/runner/use", request_body or None, {
                "Content-Type": "application/octet-stream",
                "X-Vault-Proxy-Metadata": encoded,
            })
            upstream_status = body.get("status")
            upstream_body = body.get("body")
            if not isinstance(upstream_status, int) or not isinstance(upstream_body, str):
                raise BrokerError("UPSTREAM_INVALID_RESPONSE", "Vault proxy returned an invalid response")
            return {
                "status": upstream_status,
                "body": upstream_body,
                "headers": body.get("headers", {}) if isinstance(body.get("headers"), dict) else {},
                "truncated": body.get("truncated") is True,
            }
        raise BrokerError("OPERATION_NOT_ALLOWED", "Broker operation is not available")


CLIENT_SLOTS = threading.BoundedSemaphore(16)


class BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(10)
        if not CLIENT_SLOTS.acquire(blocking=False):
            self._error(None, BrokerError("BROKER_BUSY", "Broker is busy; retry the request", True))
            return
        try:
            self._handle_peer()
        finally:
            CLIENT_SLOTS.release()

    def _handle_peer(self) -> None:
        if not hasattr(socket, "SO_PEERCRED"):
            self._error(None, BrokerError("UNSUPPORTED_PLATFORM", "Peer credential verification is unavailable"))
            return
        try:
            raw = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, gid = struct.unpack("3i", raw)
        except OSError:
            self._error(None, BrokerError("PEER_UNVERIFIED", "Could not verify socket peer"))
            return
        if uid != self.server.allowed_uid:
            self._error(None, BrokerError("PEER_DENIED", "Socket peer is not authorized"))
            return
        try:
            line = self.rfile.readline(MAX_FRAME_BYTES + 1)
            if not line or len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                raise BrokerError("INVALID_FRAME", "Broker request frame is invalid")
            # Exactly one request per connection; trailing data is refused.
            if self.rfile.read(1):
                raise BrokerError("INVALID_FRAME", "Multiple frames per connection are not supported")
            try:
                request = json.loads(line[:-1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BrokerError("INVALID_FRAME", "Broker request is not valid JSON") from exc
            if not isinstance(request, dict) or set(request) - {"version", "id", "operation", "payload"}:
                raise BrokerError("INVALID_REQUEST", "Broker request has an invalid shape")
            if request.get("version") != PROTOCOL_VERSION:
                raise BrokerError("PROTOCOL_MISMATCH", "Broker protocol version is unsupported")
            request_id = request.get("id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{16,64}", request_id):
                raise BrokerError("INVALID_REQUEST", "Broker request id is invalid")
            result = self.server.client.dispatch(request.get("operation"), request)
            response = {"version": PROTOCOL_VERSION, "id": request_id, "ok": True, "result": result}
        except BrokerError as error:
            response = {
                "version": PROTOCOL_VERSION,
                "id": request.get("id") if isinstance(locals().get("request"), dict) else None,
                "ok": False,
                "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
            }
        except Exception:
            response = {
                "version": PROTOCOL_VERSION,
                "id": request.get("id") if isinstance(locals().get("request"), dict) else None,
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Broker operation failed", "retryable": False},
            }
        try:
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) <= MAX_FRAME_BYTES:
                self.wfile.write(encoded)
                self.wfile.flush()
        except OSError:
            return

    def _error(self, request_id: str | None, error: BrokerError) -> None:
        try:
            response = {"version": PROTOCOL_VERSION, "id": request_id, "ok": False,
                        "error": {"code": error.code, "message": error.message, "retryable": error.retryable}}
            self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            self.wfile.flush()
        except OSError:
            pass


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 32

    def __init__(self, socket_path: str, client: VaultClient, allowed_uid: int):
        self.client = client
        self.allowed_uid = allowed_uid
        super().__init__(socket_path, BrokerHandler)


def serve(args: argparse.Namespace) -> None:
    if args.bootstrap_stdin:
        try:
            bootstrap = json.loads(sys.stdin.readline(16 * 1024 + 1))
        except (json.JSONDecodeError, OSError):
            raise SystemExit("Vault broker bootstrap input is invalid")
        finally:
            try: sys.stdin.close()
            except OSError: pass
        if not isinstance(bootstrap, dict) or set(bootstrap) != {"vault_url", "oidc_request_url", "oidc_request_token", "allowed_uid"}:
            raise SystemExit("Vault broker bootstrap input has an invalid shape")
        vault_url = bootstrap["vault_url"]
        oidc_url = bootstrap["oidc_request_url"]
        oidc_token = bootstrap["oidc_request_token"]
        allowed_uid = bootstrap["allowed_uid"]
    else:
        vault_url = os.environ.get("VAULT_URL", "")
        oidc_url = os.environ.get("BROKER_OIDC_REQUEST_URL", "")
        oidc_token = os.environ.get("BROKER_OIDC_REQUEST_TOKEN", "")
        allowed_uid = args.allowed_uid
    if not isinstance(allowed_uid, int) or allowed_uid < 1:
        raise SystemExit("Vault broker peer UID is invalid")
    socket_path = Path(args.socket).resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_symlink():
        mode = socket_path.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise SystemExit("Refusing to replace a non-socket broker path")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(socket_path))
        except OSError:
            socket_path.unlink(missing_ok=True)
        else:
            raise SystemExit("A Vault broker is already listening")
        finally:
            probe.close()
    if not vault_url or not oidc_url or not oidc_token:
        raise SystemExit("Vault broker requires trusted Vault/OIDC bootstrap configuration")
    client = VaultClient(vault_url, oidc_url, oidc_token, allow_local_http=args.allow_local_http)
    # The OIDC requester is never written to disk or printed. It is retained only
    # in this dedicated UID's process memory so it can mint a fresh short session.
    server = ThreadedUnixServer(str(socket_path), client, allowed_uid)
    os.chmod(socket_path, 0o660)
    pid_file = Path(args.pid_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()) + "\n")
    os.chmod(pid_file, 0o600)
    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    import signal
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print("VAULT_BROKER=LISTENING", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)
        print("VAULT_BROKER=STOPPED", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    command = sub.add_parser("serve")
    command.add_argument("--socket", default="/run/runner-vault/broker.sock")
    command.add_argument("--pid-file", default="/run/runner-vault/broker.pid")
    command.add_argument("--allowed-uid", type=int, default=0)
    command.add_argument("--bootstrap-stdin", action="store_true", help=argparse.SUPPRESS)
    command.add_argument("--allow-local-http", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == "serve":
        serve(args)


if __name__ == "__main__":
    main()
