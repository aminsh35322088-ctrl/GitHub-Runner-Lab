#!/usr/bin/env bash
set -euo pipefail

MINUTES="${1:-60}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
LOG_FILE="${RDC_LOG_FILE:-/tmp/rdc.log}"
RESTARTS=0
MAX_RESTARTS=3

if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || (( MINUTES < 1 || MINUTES > 300 )); then
  echo "Duration must be an integer between 1 and 300 minutes."
  exit 2
fi

echo "Keeping lab alive for $MINUTES minute(s)."

for ((minute=1; minute<=MINUTES; minute++)); do
  sleep 60

  if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    RESTARTS=$((RESTARTS + 1))
    echo "[$minute/$MINUTES] RDC is not running; restart $RESTARTS/$MAX_RESTARTS."
    tail -n 60 "$LOG_FILE" 2>/dev/null || true

    if (( RESTARTS > MAX_RESTARTS )); then
      echo "RDC exceeded restart limit."
      exit 1
    fi

    ./scripts/start-rdc.sh
  elif (( minute == 1 || minute % 10 == 0 || minute == MINUTES )); then
    PID=$(cat "$PID_FILE")
    RSS=$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)
    echo "[$minute/$MINUTES] RDC healthy pid=$PID rss_kb=${RSS:-unknown}."
  fi
done

echo "Lab keepalive completed."
