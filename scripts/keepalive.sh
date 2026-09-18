#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"

MINUTES="${1:-330}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
DEVICE_FILE="$HOME/.desktop-commander-device/device.json"
RESTARTS=0
MAX_RESTARTS=3

if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || (( MINUTES < 1 || MINUTES > 330 )); then
  echo "Duration must be an integer between 1 and 330 minutes."
  exit 2
fi

hash_state() {
  if [[ -s "$DEVICE_FILE" ]]; then
    sha256sum "$DEVICE_FILE" | awk '{print $1}'
  fi
}

LAST_HASH="$(hash_state)"
CHECKPOINT_DONE=false
CHECKPOINT_MINUTE=$((MINUTES - 30))
((CHECKPOINT_MINUTE < 1)) && CHECKPOINT_MINUTE=1

./scripts/agent-runtime.sh start "$MINUTES" >/dev/null
echo "Keeping RDC Lab online for $MINUTES minute(s). Automatic checkpoint at minute $CHECKPOINT_MINUTE."

TOTAL_TICKS=$((MINUTES * 6))
for ((tick=1; tick<=TOTAL_TICKS; tick++)); do
  sleep 10
  minute=$(((tick + 5) / 6))

  if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    # Persist whatever token state was written before the crash before trying a restart.
    if [[ -s "$DEVICE_FILE" ]]; then
      ./scripts/persist-rdc-state.sh || true
      LAST_HASH="$(hash_state)"
    fi

    RESTARTS=$((RESTARTS + 1))
    echo "[$minute/$MINUTES] RDC stopped; automatic restart $RESTARTS/$MAX_RESTARTS."
    if (( RESTARTS > MAX_RESTARTS )); then
      echo "RDC exceeded the local restart limit; ending this runner so the watchdog can replace it."
      exit 1
    fi
    ./scripts/start-rdc.sh
  fi

  CURRENT_HASH="$(hash_state)"
  if [[ -n "$CURRENT_HASH" && "$CURRENT_HASH" != "$LAST_HASH" ]]; then
    echo "[$minute/$MINUTES] RDC session state changed; persisting rotated credentials."
    ./scripts/persist-rdc-state.sh
    LAST_HASH="$CURRENT_HASH"
  fi

  if [[ "$CHECKPOINT_DONE" == "false" ]] && (( minute >= CHECKPOINT_MINUTE )); then
    echo "[$minute/$MINUTES] Handoff window approaching; creating agent checkpoint."
    ./scripts/agent-checkpoint.sh auto-pre-handoff || echo "::warning::Agent checkpoint failed."
    CHECKPOINT_DONE=true
  fi

  if (( tick == 1 || tick % 6 == 0 || tick == TOTAL_TICKS )); then
    PID="$(cat "$PID_FILE")"
    RSS="$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)"
    echo "[$minute/$MINUTES] RDC healthy pid=$PID rss_kb=${RSS:-unknown}."
  fi
done

echo "RDC Lab keepalive window completed."
