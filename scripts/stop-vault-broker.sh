#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo 'stop-vault-broker.sh must run as root.' >&2
  exit 2
fi
PID_FILE="${VAULT_BROKER_PID_FILE:-/run/runner-vault/broker.pid}"
PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ "$PID" =~ ^[0-9]+$ ]]; then
  EXPECTED_UID="$(id -u "${VAULT_BROKER_USER:-runner-vault-broker}")"
  ACTUAL_UID="$(awk '/^Uid:/ {print $2}' "/proc/$PID/status" 2>/dev/null || true)"
  if [[ "$ACTUAL_UID" == "$EXPECTED_UID" ]]; then
    kill -TERM "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL "$PID" 2>/dev/null || true
  fi
fi
rm -f "$PID_FILE" "${VAULT_BROKER_SOCKET:-/run/runner-vault/broker.sock}"
echo 'VAULT_BROKER=STOPPED'
