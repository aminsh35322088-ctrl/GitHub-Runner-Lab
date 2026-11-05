#!/usr/bin/env bash
set -Eeuo pipefail
unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN
report_state() {
  local state="$1" detail="$2"
  echo "VAULT_RUNTIME=$state"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then printf 'state=%s\n' "${state,,}" >> "$GITHUB_OUTPUT"; fi
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    { echo "### Credential Vault Broker"; echo; echo "- Status: **$state**"; echo "- $detail"; echo "- RDC and Tailscale remain independent of optional Vault readiness."; echo; } >> "$GITHUB_STEP_SUMMARY"
  fi
}
degraded() {
  local reason="$1"
  echo "::warning::Credential Vault Broker verification failed: $reason. Agent Vault operations remain unavailable; RDC and Tailscale continue."
  report_state DEGRADED "$reason"
  exit 0
}
if [[ -z "${VAULT_URL:-}" ]]; then
  report_state DISABLED "No Vault deployment is configured. The Agent remains isolated and runs without Vault access."
  exit 0
fi
if [[ "${VAULT_CONNECT_STATE:-}" != "true" ]]; then degraded "Agent-side Broker status/registry verification did not succeed"; fi
AGENT_USER="${RDC_AGENT_USER:-rdc-agent}"
BROKER_USER="${VAULT_BROKER_USER:-runner-vault-broker}"
CLIENT_GROUP="${VAULT_BROKER_GROUP:-rdc-vault-client}"
AGENT_HOME="${RDC_AGENT_HOME:-/var/lib/rdc-agent}"
BROKER_HOME="${VAULT_BROKER_HOME:-/var/lib/runner-vault-broker}"
SOCKET="${VAULT_BROKER_SOCKET:-/run/runner-vault/broker.sock}"
BROKER_PID_FILE="${VAULT_BROKER_PID_FILE:-/run/runner-vault/broker.pid}"
id "$AGENT_USER" >/dev/null 2>&1 || degraded "RDC Agent system identity is missing"
id "$BROKER_USER" >/dev/null 2>&1 || degraded "Vault Broker system identity is missing"
AGENT_UID="$(id -u "$AGENT_USER")"
BROKER_UID="$(id -u "$BROKER_USER")"
[[ "$AGENT_UID" != "$BROKER_UID" && "$AGENT_UID" != "$(id -u)" && "$BROKER_UID" != "$(id -u)" ]] || degraded "Runner, Agent and Broker do not have separate UIDs"
for identity in "$AGENT_USER" "$BROKER_USER"; do
  if id -nG "$identity" | tr ' ' '\n' | grep -Exq 'docker|sudo'; then degraded "$identity belongs to a privileged docker/sudo group"; fi
done
id -nG "$AGENT_USER" | tr ' ' '\n' | grep -Fxq "$CLIENT_GROUP" || degraded "RDC Agent is not in the restricted Broker client group"
id -nG "$BROKER_USER" | tr ' ' '\n' | grep -Fxq "$CLIENT_GROUP" || degraded "Broker is not in the restricted Broker socket group"
[[ -S "$SOCKET" && ! -L "$SOCKET" ]] || degraded "Broker Unix socket is missing or is not a socket"
SOCKET_MODE="$(stat -c '%a' "$SOCKET")"
SOCKET_GROUP="$(stat -c '%G' "$SOCKET")"
[[ "$SOCKET_MODE" == "660" && "$SOCKET_GROUP" == "$CLIENT_GROUP" ]] || degraded "Broker socket mode/group is not 0660:$CLIENT_GROUP"
DIR_MODE="$(stat -c '%a' "$(dirname "$SOCKET")")"
DIR_GROUP="$(stat -c '%G' "$(dirname "$SOCKET")")"
[[ "$DIR_MODE" == "750" && "$DIR_GROUP" == "$CLIENT_GROUP" ]] || degraded "Broker socket directory is not 0750:$CLIENT_GROUP"
BROKER_PID="$(sudo -n -u "$BROKER_USER" -- cat "$BROKER_PID_FILE" 2>/dev/null || true)"
[[ "$BROKER_PID" =~ ^[0-9]+$ && -r "/proc/$BROKER_PID/status" ]] || degraded "Broker process PID is unavailable"
ACTUAL_BROKER_UID="$(awk '/^Uid:/ {print $2}' "/proc/$BROKER_PID/status")"
[[ "$ACTUAL_BROKER_UID" == "$BROKER_UID" ]] || degraded "Broker process is not running under the dedicated Broker UID"
if ! sudo -n -u "$BROKER_USER" -- python3 - "$BROKER_PID" <<'PY_BROKER_ENV'
from pathlib import Path
import sys
pid = sys.argv[1]
env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
keys = {entry.split(b"=", 1)[0] for entry in env if b"=" in entry}
argv = Path(f"/proc/{pid}/cmdline").read_bytes()
for key in (b"ACTIONS_ID_TOKEN_REQUEST_URL", b"ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            b"BROKER_BOOTSTRAP_OIDC_URL", b"BROKER_BOOTSTRAP_OIDC_TOKEN",
            b"BROKER_OIDC_REQUEST_URL", b"BROKER_OIDC_REQUEST_TOKEN", b"VAULT_URL"):
    present = key in keys
    print(f"VAULT_BROKER_{key.decode()}={'PRESENT' if present else 'ABSENT'}")
    if present: raise SystemExit(10)
if b"ACTIONS_ID_TOKEN_REQUEST" in argv or b"BROKER_BOOTSTRAP" in argv or b"Bearer " in argv:
    raise SystemExit(11)
PY_BROKER_ENV
then
  degraded "Vault Broker retained OIDC bootstrap values in its process environment or arguments"
fi
AGENT_PID="$(./scripts/run-as-rdc-agent.sh cat "$AGENT_HOME/.run/rdc.pid" 2>/dev/null || true)"
[[ "$AGENT_PID" =~ ^[0-9]+$ && -r "/proc/$AGENT_PID/status" ]] || degraded "RDC Agent process PID is unavailable"
ACTUAL_AGENT_UID="$(awk '/^Uid:/ {print $2}' "/proc/$AGENT_PID/status")"
[[ "$ACTUAL_AGENT_UID" == "$AGENT_UID" ]] || degraded "RDC process is not running under the isolated Agent UID"
if ! ./scripts/run-as-rdc-agent.sh python3 - "$AGENT_PID" <<'PY_AGENT_ENV'
from pathlib import Path
import sys
pid = sys.argv[1]
entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
keys = {entry.split(b"=", 1)[0] for entry in entries if b"=" in entry}
for key in (b"ACTIONS_ID_TOKEN_REQUEST_URL", b"ACTIONS_ID_TOKEN_REQUEST_TOKEN", b"RDC_STATE_KEY", b"GH_TOKEN", b"AGENT_GITHUB_TOKEN"):
    present = key in keys
    print(f"RDC_PROCESS_{key.decode()}={'PRESENT' if present else 'ABSENT'}")
    if present: raise SystemExit(10)
PY_AGENT_ENV
then
  degraded "RDC process inherited an OIDC, persistence or GitHub bootstrap secret"
fi
ROOTLESS="$(./scripts/run-as-rdc-agent.sh podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)"
[[ "$ROOTLESS" == "true" ]] || degraded "Agent container engine is not rootless Podman"
STATUS_FILE="$(mktemp)"
REGISTRY_FILE="$(mktemp)"
chmod 0600 "$STATUS_FILE" "$REGISTRY_FILE"
cleanup() { rm -f "$STATUS_FILE" "$REGISTRY_FILE"; }
trap cleanup EXIT
if ! ./scripts/run-as-rdc-agent.sh "$AGENT_HOME/.local/bin/vault" status > "$STATUS_FILE" 2>/dev/null; then
  degraded "Agent could not obtain Broker status through the restricted socket"
fi
jq -e '.connected == true and .brokerProtocol == 1' "$STATUS_FILE" >/dev/null || degraded "Broker status response is invalid"
if ! ./scripts/run-as-rdc-agent.sh "$AGENT_HOME/.local/bin/vault" list > "$REGISTRY_FILE" 2>/dev/null; then
  degraded "Agent could not read the metadata-only credential registry through the Broker"
fi
jq -e 'type == "array" and all(.[]; (has("value")|not) and (has("value_encrypted")|not) and (has("secret")|not))' "$REGISTRY_FILE" >/dev/null || degraded "Broker registry response included secret material or an invalid shape"
if sudo -n -u "$BROKER_USER" -- find "$BROKER_HOME" -maxdepth 1 -type f \( -iname '*session*' -o -iname '*token*' \) -print -quit | grep -q .; then
  degraded "Broker home contains a file matching a Session/token cache pattern"
fi
COUNT="$(jq 'length' "$REGISTRY_FILE")"
echo "VAULT_RDC_AGENT_UID=$AGENT_UID"
echo "VAULT_BROKER_UID=$BROKER_UID"
echo "VAULT_RDC_SOCKET=PEER_UID_RESTRICTED"
echo "VAULT_RDC_CONTAINER_ENGINE=ROOTLESS_PODMAN"
echo "VAULT_RDC_OIDC_REQUESTER=ABSENT"
echo "VAULT_RDC_SESSION_CACHE=ABSENT"
echo "VAULT_RDC_METADATA_REGISTRY=READY"
echo "VAULT_VISIBLE_CREDENTIALS=$COUNT"
report_state READY "RDC runs under a separate unprivileged UID; rootless Podman is active; the Broker accepts only the Agent peer UID and exposes only fixed, policy-enforced operations. OIDC and Runner Session material remain inside the Broker process memory."
