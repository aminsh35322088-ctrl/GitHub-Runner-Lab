#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${VAULT_URL:-}" ]]; then
  echo "VAULT_RUNTIME=DISABLED (RUNNER_VAULT_URL is not configured)"
  exit 0
fi

CLIENT="${VAULT_CLIENT_BIN:-$HOME/.local/bin/vault}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
[[ -x "$CLIENT" ]] || { echo '::error::Vault URL is configured but the client is not installed.'; exit 1; }
[[ -s "$PID_FILE" ]] || { echo '::error::RDC PID file is missing.'; exit 1; }
PID="$(cat "$PID_FILE")"
[[ "$PID" =~ ^[0-9]+$ && -r "/proc/$PID/environ" ]] || { echo '::error::RDC process environment cannot be inspected.'; exit 1; }

python3 - "$PID" "$VAULT_URL" <<'PY'
import sys
from pathlib import Path

pid, expected_url = sys.argv[1], sys.argv[2]
try:
    entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    env = {}
    for item in entries:
        if b"=" in item:
            key, value = item.split(b"=", 1)
            env[key.decode(errors="ignore")] = value.decode(errors="ignore")
except OSError:
    print("::error::Cannot inspect the RDC process environment.")
    raise SystemExit(1)

checks = {
    "vault_url": env.get("VAULT_URL") == expected_url,
    "oidc_request_url": bool(env.get("ACTIONS_ID_TOKEN_REQUEST_URL")),
    "oidc_request_token": bool(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")),
}
for name, ok in checks.items():
    print(f"VAULT_RDC_{name.upper()}={'READY' if ok else 'MISSING'}")
if not all(checks.values()):
    print("::error::RDC did not inherit the Vault URL and GitHub OIDC request environment. The runner client cannot authenticate from agent sessions.")
    raise SystemExit(1)
print("VAULT_RUNTIME=READY")
PY
