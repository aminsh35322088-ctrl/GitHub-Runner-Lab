#!/usr/bin/env bash
set -Eeuo pipefail

report_state() {
  local state="$1"
  local detail="$2"

  echo "VAULT_RUNTIME=$state"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'state=%s\n' "${state,,}" >> "$GITHUB_OUTPUT"
  fi
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "### Credential Vault"
      echo
      echo "- Status: **$state**"
      echo "- $detail"
      echo "- RDC and Tailscale connectivity continue independently of Vault readiness."
      echo
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

degraded() {
  echo "::warning::Credential Vault is configured but not ready inside RDC. Vault operations remain unavailable; RDC and Tailscale startup will continue."
  report_state DEGRADED "Vault client/session integration is not ready; check the sanitized readiness markers above."
}

if [[ -z "${VAULT_URL:-}" ]]; then
  report_state DISABLED "Optional Vault integration is not configured."
  exit 0
fi

CLIENT="${VAULT_CLIENT_BIN:-$HOME/.local/bin/vault}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"

if [[ ! -x "$CLIENT" ]]; then
  echo "VAULT_RDC_CLIENT=MISSING"
  degraded
  exit 0
fi

if [[ ! -s "$PID_FILE" ]]; then
  echo "VAULT_RDC_PID_FILE=MISSING"
  degraded
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ ! "$PID" =~ ^[0-9]+$ || ! -r "/proc/$PID/environ" ]]; then
  echo "VAULT_RDC_PROCESS_ENV=UNAVAILABLE"
  degraded
  exit 0
fi

set +e
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
except Exception:
    print("VAULT_RDC_PROCESS_ENV=UNAVAILABLE")
    raise SystemExit(20)

checks = {
    "vault_url": env.get("VAULT_URL") == expected_url,
    "oidc_request_url": bool(env.get("ACTIONS_ID_TOKEN_REQUEST_URL")),
    "oidc_request_token": bool(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")),
}
for name, ok in checks.items():
    print(f"VAULT_RDC_{name.upper()}={'READY' if ok else 'MISSING'}")
if not all(checks.values()):
    raise SystemExit(10)
PY
result=$?
set -e

case "$result" in
  0)
    report_state READY "Vault URL and job-scoped OIDC requester are present in the RDC process."
    ;;
  10|20)
    degraded
    ;;
  *)
    echo "::warning::Vault runtime verifier encountered an unexpected internal failure; RDC and Tailscale startup will continue."
    report_state DEGRADED "Runtime verification could not complete; inspect the verifier step for a sanitized diagnostic."
    ;;
esac
exit 0
