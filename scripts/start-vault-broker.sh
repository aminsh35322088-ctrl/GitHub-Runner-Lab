#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo 'start-vault-broker.sh must run as root.' >&2
  exit 2
fi

SOCKET="${VAULT_BROKER_SOCKET:-/run/runner-vault/broker.sock}"
PID_FILE="${VAULT_BROKER_PID_FILE:-/run/runner-vault/broker.pid}"
LOG_FILE="${VAULT_BROKER_LOG_FILE:-/run/runner-vault/broker.log}"
BROKER_USER="${VAULT_BROKER_USER:-runner-vault-broker}"
AGENT_USER="${RDC_AGENT_USER:-rdc-agent}"
CLIENT_GROUP="${VAULT_BROKER_GROUP:-rdc-vault-client}"
BROKER_HOME="$(getent passwd "$BROKER_USER" | cut -d: -f6)"
VAULT_ORIGIN="${VAULT_URL:-}"
OIDC_REQUEST_URL="${ACTIONS_ID_TOKEN_REQUEST_URL:-}"
OIDC_REQUEST_TOKEN="${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}"
AGENT_UID="$(id -u "$AGENT_USER")"

if [[ -z "$VAULT_ORIGIN" ]]; then
  echo 'VAULT_BROKER=DISABLED'
  exit 0
fi
if [[ -z "$OIDC_REQUEST_URL" || -z "$OIDC_REQUEST_TOKEN" ]]; then
  echo '::warning::GitHub OIDC requester is unavailable; Agent Vault operations remain disabled.'
  echo 'VAULT_BROKER=DEGRADED'
  exit 0
fi

if id -nG "$BROKER_USER" | tr ' ' '\n' | grep -Exq 'docker|sudo'; then
  echo 'Vault broker unexpectedly has a privileged group.' >&2
  exit 4
fi
install -d -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0750 "$(dirname "$SOCKET")"
install -d -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0700 "$BROKER_HOME"
install -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0500 \
  "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}/scripts/vault_broker.py" "$BROKER_HOME/vault_broker.py"
install -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0600 /dev/null "$LOG_FILE"

if [[ -S "$SOCKET" ]]; then
  if python3 - "$SOCKET" <<'PY_SOCKET_PROBE'
import socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(.2)
try: s.connect(sys.argv[1]); raise SystemExit(0)
except OSError: raise SystemExit(1)
finally: s.close()
PY_SOCKET_PROBE
  then
    echo 'A Vault broker is already listening.' >&2
    exit 3
  fi
  rm -f -- "$SOCKET"
fi

# The bootstrap is passed over an anonymous pipe, never argv, a file or the
# Broker process environment. Its long-lived UID keeps the values only in RAM.
export BROKER_BOOTSTRAP_VAULT_URL="$VAULT_ORIGIN"
export BROKER_BOOTSTRAP_OIDC_URL="$OIDC_REQUEST_URL"
export BROKER_BOOTSTRAP_OIDC_TOKEN="$OIDC_REQUEST_TOKEN"
export BROKER_BOOTSTRAP_ALLOWED_UID="$AGENT_UID"
unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN

/usr/bin/python3 -c 'import json,os; print(json.dumps({"vault_url":os.environ["BROKER_BOOTSTRAP_VAULT_URL"],"oidc_request_url":os.environ["BROKER_BOOTSTRAP_OIDC_URL"],"oidc_request_token":os.environ["BROKER_BOOTSTRAP_OIDC_TOKEN"],"allowed_uid":int(os.environ["BROKER_BOOTSTRAP_ALLOWED_UID"])},separators=(",",":")))' |
  env -u ACTIONS_ID_TOKEN_REQUEST_URL -u ACTIONS_ID_TOKEN_REQUEST_TOKEN \
      -u BROKER_BOOTSTRAP_VAULT_URL -u BROKER_BOOTSTRAP_OIDC_URL -u BROKER_BOOTSTRAP_OIDC_TOKEN -u BROKER_BOOTSTRAP_ALLOWED_UID \
  runuser -u "$BROKER_USER" -- env -i \
    HOME="$BROKER_HOME" \
    USER="$BROKER_USER" LOGNAME="$BROKER_USER" PATH=/usr/bin:/bin \
    /usr/bin/python3 "$BROKER_HOME/vault_broker.py" serve \
    --socket "$SOCKET" --pid-file "$PID_FILE" --bootstrap-stdin \
    >> "$LOG_FILE" 2>&1 &
BROKER_PID=$!
unset BROKER_BOOTSTRAP_VAULT_URL BROKER_BOOTSTRAP_OIDC_URL BROKER_BOOTSTRAP_OIDC_TOKEN BROKER_BOOTSTRAP_ALLOWED_UID OIDC_REQUEST_URL OIDC_REQUEST_TOKEN

for _ in $(seq 1 50); do
  if [[ -S "$SOCKET" ]]; then
    chmod 0660 "$SOCKET"
    chgrp "$CLIENT_GROUP" "$SOCKET"
    echo 'VAULT_BROKER=STARTING'
    break
  fi
  if ! kill -0 "$BROKER_PID" 2>/dev/null; then
    echo '::warning::Vault broker exited during startup; Agent Vault operations remain disabled.'
    echo 'VAULT_BROKER=DEGRADED'
    tail -n 10 "$LOG_FILE" | sed -E 's/(token|secret|bearer)[^ ]*/[redacted]/Ig' >&2
    exit 0
  fi
  sleep 0.1
done

if [[ ! -S "$SOCKET" ]]; then
  echo '::warning::Vault broker did not create its IPC socket.'
  echo 'VAULT_BROKER=DEGRADED'
  exit 0
fi

if ! runuser -u "$AGENT_USER" -- /usr/bin/python3 - "$SOCKET" <<'PY_AGENT_PROBE'
import socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(2); s.connect(sys.argv[1]); s.close()
PY_AGENT_PROBE
then
  echo '::warning::RDC Agent could not connect to the Vault broker socket.'
  echo 'VAULT_BROKER=DEGRADED'
  kill "$BROKER_PID" 2>/dev/null || true
  exit 0
fi

echo 'VAULT_BROKER=READY'
