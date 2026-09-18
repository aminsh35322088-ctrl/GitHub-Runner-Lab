#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"

MINUTES="${1:-330}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
DEVICE_FILE="$HOME/.desktop-commander-device/device.json"
RESTARTS=0
MAX_RESTARTS=3
CHECKPOINT_DONE=false

if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || (( MINUTES < 1 || MINUTES > 330 )); then
  echo "Duration must be an integer between 1 and 330 minutes."
  exit 2
fi

hash_state() {
  if [[ -s "$DEVICE_FILE" ]]; then
    sha256sum "$DEVICE_FILE" | awk '{print $1}'
  fi
}

runtime_value() {
  local key="$1"
  ./scripts/agent-runtime.sh status | awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}'
}

if [[ "$(runtime_value RUNTIME_STATE)" == "UNKNOWN" ]]; then
  # Fallback for manual/local invocation. Normal workflow runs initialize the
  # lifecycle clock immediately after checkout so setup time counts too.
  ./scripts/agent-runtime.sh start "$MINUTES" >/dev/null
fi

LAST_HASH="$(hash_state)"
echo "Keeping RDC Lab online until the runner lifecycle clock reaches handoff."
./scripts/agent-runtime.sh status

TOTAL_TICKS=$((MINUTES * 6))
for ((tick=1; tick<=TOTAL_TICKS; tick++)); do
  sleep 10
  minute=$(((tick + 5) / 6))

  if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    if [[ -s "$DEVICE_FILE" ]]; then
      ./scripts/persist-rdc-state.sh || true
      LAST_HASH="$(hash_state)"
    fi

    RESTARTS=$((RESTARTS + 1))
    echo "[$minute] RDC stopped; automatic restart $RESTARTS/$MAX_RESTARTS."
    if (( RESTARTS > MAX_RESTARTS )); then
      echo "RDC exceeded the local restart limit; ending this runner so the watchdog can replace it."
      exit 1
    fi
    ./scripts/start-rdc.sh
  fi

  CURRENT_HASH="$(hash_state)"
  if [[ -n "$CURRENT_HASH" && "$CURRENT_HASH" != "$LAST_HASH" ]]; then
    echo "[$minute] RDC session state changed; persisting rotated credentials."
    ./scripts/persist-rdc-state.sh
    LAST_HASH="$CURRENT_HASH"
  fi

  if (( tick == 1 || tick % 6 == 0 || tick == TOTAL_TICKS )); then
    RUNTIME="$(./scripts/agent-runtime.sh status)"
    STATE="$(awk -F= '$1=="RUNTIME_STATE"{print $2}' <<<"$RUNTIME")"
    REMAINING="$(awk -F= '$1=="REMAINING_MINUTES"{print $2}' <<<"$RUNTIME")"
    ELAPSED="$(awk -F= '$1=="ELAPSED_MINUTES"{print $2}' <<<"$RUNTIME")"

    case "$STATE" in
      CHECKPOINT_REQUIRED|HANDOFF_IMMINENT|HANDOFF_DUE)
        if [[ "$CHECKPOINT_DONE" == "false" ]]; then
          echo "[$minute] Handoff window approaching; creating agent checkpoint."
          ./scripts/agent-checkpoint.sh auto-pre-handoff || echo "::warning::Agent checkpoint failed."
          CHECKPOINT_DONE=true
        fi
        ;;
    esac

    PID="$(cat "$PID_FILE")"
    RSS="$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)"
    echo "[$minute] RDC healthy pid=$PID rss_kb=${RSS:-unknown} lifecycle=${STATE:-UNKNOWN} elapsed=${ELAPSED:-?}m remaining=${REMAINING:-?}m."

    if [[ "$STATE" == "HANDOFF_DUE" ]]; then
      echo "Runner lifecycle handoff deadline reached; ending keepalive cleanly."
      break
    fi
  fi
done

echo "RDC Lab keepalive window completed."
