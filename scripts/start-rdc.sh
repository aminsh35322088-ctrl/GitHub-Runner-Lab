#!/usr/bin/env bash
set -euo pipefail

RDC_DIR="$HOME/.desktop-commander-device"
DEVICE_FILE="$RDC_DIR/device.json"
LOG_FILE="${RDC_LOG_FILE:-/tmp/rdc.log}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"

[[ -s "$DEVICE_FILE" ]] || { echo "RDC device state was not restored."; exit 2; }
mkdir -p "$RDC_DIR"
chmod 700 "$RDC_DIR"
chmod 600 "$DEVICE_FILE"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "RDC is already running."
  exit 0
fi

: > "$LOG_FILE"
nohup env   NODE_OPTIONS=--dns-result-order=ipv4first   RUNNER_TRACKING_ID=rdc-runner-lab   desktop-commander remote   >"$LOG_FILE" 2>&1 < /dev/null &

PID=$!
echo "$PID" > "$PID_FILE"
echo "RDC agent started; waiting for authenticated readiness."

for _ in $(seq 1 30); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "RDC exited during startup. Check the private state/bootstrap configuration."
    exit 1
  fi
  if grep -q 'Device ready' "$LOG_FILE" 2>/dev/null; then
    echo "RDC authenticated and ready."
    exit 0
  fi
  if grep -q 'Please complete authentication' "$LOG_FILE" 2>/dev/null; then
    echo "RDC requires interactive re-authorization; stored identity is no longer valid."
    exit 1
  fi
  sleep 1
done

echo "RDC did not reach ready state within 30 seconds."
exit 1
