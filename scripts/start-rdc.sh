#!/usr/bin/env bash
set -euo pipefail

RDC_DIR="$HOME/.desktop-commander-device"
LOG_FILE="${RDC_LOG_FILE:-/tmp/rdc.log}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"

mkdir -p "$RDC_DIR"
chmod 700 "$RDC_DIR"

if [[ -n "${RDC_DEVICE_STATE_B64:-}" ]]; then
  printf '%s' "$RDC_DEVICE_STATE_B64" | base64 --decode > "$RDC_DIR/device.json"
  chmod 600 "$RDC_DIR/device.json"
  echo "Restored RDC device identity from GitHub Actions secret."
else
  echo "No RDC_DEVICE_STATE_B64 secret found; manual pairing will be required."
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "RDC is already running with PID $(cat "$PID_FILE")."
  exit 0
fi

: > "$LOG_FILE"
nohup env \
  NODE_OPTIONS=--dns-result-order=ipv4first \
  RUNNER_TRACKING_ID=rdc-runner-lab \
  desktop-commander remote \
  >"$LOG_FILE" 2>&1 < /dev/null &

PID=$!
echo "$PID" > "$PID_FILE"
echo "Started RDC with PID $PID."
sleep 6

if ! kill -0 "$PID" 2>/dev/null; then
  echo "RDC exited during startup."
  tail -n 120 "$LOG_FILE" || true
  exit 1
fi

tail -n 80 "$LOG_FILE" || true
