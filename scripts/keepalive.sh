#!/usr/bin/env bash
set -Eeuo pipefail

MINUTES="${1:-330}"
RDC_ENABLED="${RDC_ENABLED:-true}"
CLOUDFLARE_SSH_ENABLED="${CLOUDFLARE_SSH_ENABLED:-false}"
if [[ "$RDC_ENABLED" == "true" || -n "${RDC_STATE_KEY:-}" ]]; then
  : "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"
fi
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
DEVICE_FILE="$HOME/.desktop-commander-device/device.json"
REQUEST_FILE="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}/handoff.request"
RESTARTS=0
MAX_RESTARTS=3
CHECKPOINT_DONE=false
LAST_CHECKPOINT=0
HEALTH_FAILURES=0
CLOUDFLARE_HEALTH_FAILURES=0
CLOUDFLARE_RESTARTS=0
SUCCESSOR_QUEUED=false

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

queue_successor() {
  if [[ "$SUCCESSOR_QUEUED" == "true" ]]; then
    return 0
  fi
  if [[ -z "${GH_TOKEN:-}" || -z "${REPO:-}" ]]; then
    echo "::warning::GitHub Actions credentials are unavailable inside keepalive; the normal handover/watchdog step will queue the successor."
    return 0
  fi
  if bash .github/scripts/ensure-rdc-lab.sh; then
    SUCCESSOR_QUEUED=true
    echo "Successor run confirmed/queued before ending this runner."
  else
    echo "::warning::Could not pre-queue successor; the normal handover/watchdog step will retry."
  fi
}

create_checkpoint() {
  if [[ "$CHECKPOINT_DONE" == "false" ]]; then
    echo "Creating agent checkpoint before runner handoff."
    python3 scripts/lab_jobs.py drain --seconds "${AGENT_DRAIN_SECONDS:-900}" || echo "::warning::Job drain incomplete."
    ./scripts/checkpoint-sync.sh save auto-pre-handoff || echo "::warning::Durable checkpoint failed; finalizer will retry."
    CHECKPOINT_DONE=true
  fi
}

if [[ "$(runtime_value RUNTIME_STATE)" == "UNKNOWN" ]]; then
  ./scripts/agent-runtime.sh start "$MINUTES" >/dev/null
fi

LAST_HASH="$(hash_state)"
echo "Keeping Runner Lab online until the lifecycle enters the final auto-restart window."
./scripts/agent-runtime.sh status

TOTAL_TICKS=$((MINUTES * 6))
for ((tick=1; tick<=TOTAL_TICKS; tick++)); do
  sleep 10
  minute=$(((tick + 5) / 6))

  if [[ "$RDC_ENABLED" == "true" ]]; then
    if ./scripts/health.sh >/dev/null 2>&1; then
      HEALTH_FAILURES=0
    else
      HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi
    if (( HEALTH_FAILURES >= 3 )); then
      if [[ -s "$DEVICE_FILE" ]]; then
        ./scripts/persist-rdc-state.sh || true
        LAST_HASH="$(hash_state)"
      fi

      RESTARTS=$((RESTARTS + 1))
      echo "[$minute] RDC stopped; automatic RDC process restart $RESTARTS/$MAX_RESTARTS."
      if (( RESTARTS > MAX_RESTARTS )); then
        echo "RDC exceeded the local restart limit; ending this runner so the watchdog can replace it."
        exit 1
      fi
      ./scripts/stop-rdc.sh || true
      ./scripts/start-rdc.sh
      HEALTH_FAILURES=0
    fi
  fi

  if [[ "$CLOUDFLARE_SSH_ENABLED" == "true" ]]; then
    if ./scripts/cloudflare-ssh.sh health >/dev/null 2>&1; then
      CLOUDFLARE_HEALTH_FAILURES=0
    else
      CLOUDFLARE_HEALTH_FAILURES=$((CLOUDFLARE_HEALTH_FAILURES + 1))
    fi
    if (( CLOUDFLARE_HEALTH_FAILURES >= 3 )); then
      CLOUDFLARE_RESTARTS=$((CLOUDFLARE_RESTARTS + 1))
      echo "[$minute] Cloudflare SSH unhealthy; automatic restart $CLOUDFLARE_RESTARTS/$MAX_RESTARTS."
      if (( CLOUDFLARE_RESTARTS > MAX_RESTARTS )); then
        echo "Cloudflare SSH exceeded the local restart limit; ending this runner so the watchdog can replace it."
        exit 1
      fi
      ./scripts/cloudflare-ssh.sh stop || true
      ./scripts/cloudflare-ssh.sh start
      CLOUDFLARE_HEALTH_FAILURES=0
    fi
  fi

  now="$(date +%s)"
  if (( now - LAST_CHECKPOINT >= ${AGENT_CHECKPOINT_INTERVAL_SECONDS:-900} )); then
    ./scripts/checkpoint-sync.sh save periodic || echo '::warning::Periodic durable checkpoint failed.'
    LAST_CHECKPOINT=$now
  fi

  CURRENT_HASH="$(hash_state)"
  if [[ -n "$CURRENT_HASH" && "$CURRENT_HASH" != "$LAST_HASH" ]]; then
    echo "[$minute] RDC session state changed; persisting rotated credentials."
    ./scripts/persist-rdc-state.sh
    LAST_HASH="$CURRENT_HASH"
  fi

  if [[ -f "$REQUEST_FILE" ]]; then
    echo "[$minute] Agent requested a clean runner restart."
    create_checkpoint
    queue_successor
    break
  fi

  if (( tick == 1 || tick % 6 == 0 || tick == TOTAL_TICKS )); then
    RUNTIME="$(./scripts/agent-runtime.sh status)"
    STATE="$(awk -F= '$1=="RUNTIME_STATE"{print $2}' <<<"$RUNTIME")"
    REMAINING="$(awk -F= '$1=="REMAINING_MINUTES"{print $2}' <<<"$RUNTIME")"
    AUTO_HANDOFF="$(awk -F= '$1=="AUTO_HANDOFF_MINUTES"{print $2}' <<<"$RUNTIME")"
    ELAPSED="$(awk -F= '$1=="ELAPSED_MINUTES"{print $2}' <<<"$RUNTIME")"
    AUTO_HANDOFF="${AUTO_HANDOFF:-20}"

    if [[ "$RDC_ENABLED" == "true" && -s "$PID_FILE" ]]; then
      PID="$(cat "$PID_FILE")"
      RSS="$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)"
      echo "[$minute] RDC healthy pid=$PID rss_kb=${RSS:-unknown} lifecycle=${STATE:-UNKNOWN} elapsed=${ELAPSED:-?}m remaining=${REMAINING:-?}m."
    else
      echo "[$minute] Runner healthy access=cloudflare-ssh lifecycle=${STATE:-UNKNOWN} elapsed=${ELAPSED:-?}m remaining=${REMAINING:-?}m."
    fi

    if [[ "$STATE" == "HANDOFF_DUE" ]] || { [[ "$REMAINING" =~ ^[0-9]+$ ]] && (( REMAINING <= AUTO_HANDOFF )); }; then
      echo "[$minute] Final ${AUTO_HANDOFF}m restart window reached; checkpointing and rotating to a fresh runner."
      create_checkpoint
      queue_successor
      break
    fi
  fi
done

echo "Runner Lab keepalive window completed; workflow finalizers will persist state and release the successor."
