#!/usr/bin/env bash
set -euo pipefail

PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No RDC PID file; nothing to stop."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "RDC process is already stopped."
  exit 0
fi

echo "Stopping RDC gracefully so the newest rotated session is persisted."
kill -TERM "$PID"

for _ in $(seq 1 15); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "RDC stopped cleanly."
    exit 0
  fi
  sleep 1
done

echo "RDC did not stop within 15 seconds; forcing termination."
kill -KILL "$PID" 2>/dev/null || true
