#!/usr/bin/env bash
set -euo pipefail

MINUTES="${1:-330}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
RESTARTS=0
MAX_RESTARTS=3

if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || (( MINUTES < 1 || MINUTES > 330 )); then
  echo "Duration must be an integer between 1 and 330 minutes."
  exit 2
fi

echo "Keeping RDC Lab online for $MINUTES minute(s)."

for ((minute=1; minute<=MINUTES; minute++)); do
  sleep 60

  if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    RESTARTS=$((RESTARTS + 1))
    echo "[$minute/$MINUTES] RDC stopped; automatic restart $RESTARTS/$MAX_RESTARTS."
    if (( RESTARTS > MAX_RESTARTS )); then
      echo "RDC exceeded the local restart limit; ending this runner so the watchdog can replace it."
      exit 1
    fi
    ./scripts/start-rdc.sh
  fi

  if (( minute % 5 == 0 )); then
    ./scripts/save-rdc-state.sh
  fi

  if (( minute == 1 || minute % 10 == 0 || minute == MINUTES )); then
    PID="$(cat "$PID_FILE")"
    RSS="$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)"
    echo "[$minute/$MINUTES] RDC healthy pid=$PID rss_kb=${RSS:-unknown}."
  fi
done

echo "RDC Lab keepalive window completed."
