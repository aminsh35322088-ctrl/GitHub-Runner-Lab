#!/usr/bin/env bash
set -Eeuo pipefail

unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN
: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"

MINUTES="${1:-330}"
RDC_AGENT_USER="${RDC_AGENT_USER:-rdc-agent}"
RDC_AGENT_HOME="${RDC_AGENT_HOME:-$(getent passwd "$RDC_AGENT_USER" | cut -d: -f6)}"
DEVICE_FILE="$RDC_AGENT_HOME/.desktop-commander-device/device.json"
AGENT_CACHE="${AGENT_KIT_CACHE_DIR:-/home/runner/.cache/agent-runner-kit}"
AGENT_LAB="${RDC_AGENT_HOME}/lab"
AGENT_NODE_HOME="$RDC_AGENT_HOME/.node-runtime"
AGENT_PATH="$RDC_AGENT_HOME/.local/bin:$AGENT_NODE_HOME/bin:/usr/local/bin:/usr/bin:/bin"
AGENT_SHARED_ENV=(
  AGENT_CONTAINER_ENGINE=podman
  AGENT_WORKSPACE_ROOT="${AGENT_WORKSPACE_ROOT:-/home/runner/agent-workspaces}"
  AGENT_RECOVERY_DIR="${AGENT_RECOVERY_DIR:-/home/runner/agent-recovery}"
  AGENT_JOBS_DIR="${AGENT_JOBS_DIR:-/home/runner/agent-jobs}"
  AGENT_KIT_CACHE_DIR="$AGENT_CACHE"
  AGENT_CHECKPOINT_DIR="${AGENT_CHECKPOINT_DIR:-/home/runner/agent-checkpoints}"
  AGENT_PROJECT_CACHE_ROOT="${AGENT_PROJECT_CACHE_ROOT:-/home/runner/.cache/agent-projects}"
  AGENT_NPM_CACHE_DIR="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}"
  AGENT_CHECKPOINT_ARTIFACT_DIR="${AGENT_CHECKPOINT_ARTIFACT_DIR:-/home/runner/agent-checkpoints/artifacts}"
  npm_config_cache="${npm_config_cache:-/home/runner/.npm}"
  GITHUB_WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"
  GITHUB_RUN_ID="${GITHUB_RUN_ID:-manual}"
  GITHUB_RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
  GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"
)
REQUEST_FILE="$AGENT_CACHE/handoff.request"
PID_FILE="${RDC_PID_FILE:-$RDC_AGENT_HOME/.run/rdc.pid}"
RDC_LOG_FILE="${RDC_LOG_FILE:-$RDC_AGENT_HOME/.run/rdc.log}"
RDC_HEALTH_FILE="${RDC_HEALTH_FILE:-$RDC_AGENT_HOME/.run/rdc-health.json}"
RDC_BOOTSTRAP_LOG="${RDC_BOOTSTRAP_LOG:-$RDC_AGENT_HOME/.run/rdc-bootstrap.log}"
RESTARTS=0
MAX_RESTARTS=3
CHECKPOINT_DONE=false
LAST_CHECKPOINT=0
HEALTH_FAILURES=0
SUCCESSOR_QUEUED=false

if ! [[ "$MINUTES" =~ ^[0-9]+$ ]] || (( MINUTES < 1 || MINUTES > 330 )); then
  echo "Duration must be an integer between 1 and 330 minutes."
  exit 2
fi

AGENT_ENV=(
  HOME="$RDC_AGENT_HOME"
  USER="$RDC_AGENT_USER"
  LOGNAME="$RDC_AGENT_USER"
  PATH="$AGENT_PATH"
  XDG_RUNTIME_DIR="$RDC_AGENT_HOME/.run"
  RDC_PID_FILE="$PID_FILE"
  RDC_LOG_FILE="$RDC_LOG_FILE"
  RDC_HEALTH_FILE="$RDC_HEALTH_FILE"
  RDC_BOOTSTRAP_LOG="$RDC_BOOTSTRAP_LOG"
  NPM_CONFIG_PREFIX="$RDC_AGENT_HOME/.local"
  AGENT_SYSTEM_PACKAGES_READY=true
  AGENT_NPM_CACHE_DIR="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}"
  AGENT_CHECKPOINT_ARTIFACT_DIR="${AGENT_CHECKPOINT_ARTIFACT_DIR:-/home/runner/agent-checkpoints/artifacts}"
  AGENT_CONTAINER_ENGINE=podman
  AGENT_WORKSPACE_ROOT="${AGENT_WORKSPACE_ROOT:-/home/runner/agent-workspaces}"
  AGENT_RECOVERY_DIR="${AGENT_RECOVERY_DIR:-/home/runner/agent-recovery}"
  AGENT_JOBS_DIR="${AGENT_JOBS_DIR:-/home/runner/agent-jobs}"
  AGENT_KIT_CACHE_DIR="$AGENT_CACHE"
  AGENT_CHECKPOINT_DIR="${AGENT_CHECKPOINT_DIR:-/home/runner/agent-checkpoints}"
  AGENT_PROJECT_CACHE_ROOT="${AGENT_PROJECT_CACHE_ROOT:-/home/runner/.cache/agent-projects}"
  npm_config_cache="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}"
  GITHUB_WORKSPACE="$AGENT_LAB"
  AGENT_CHECKPOINT_ARTIFACT_DIR="${AGENT_CHECKPOINT_ARTIFACT_DIR:-/home/runner/agent-checkpoints/artifacts}"
  GITHUB_RUN_ID="${GITHUB_RUN_ID:-manual}"
  GITHUB_RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
  GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"
  CI=true
)

agent_run() {
  sudo -n -u "$RDC_AGENT_USER" -- env -i "${AGENT_ENV[@]}" \
    bash -c 'cd "$1" && shift && exec "$@"' _ "$AGENT_LAB" "$@"
}

agent_pid() {
  agent_run cat "$PID_FILE"
}

hash_state() {
  if [[ -s "$DEVICE_FILE" ]]; then
    sudo -n -u "$RDC_AGENT_USER" -- sha256sum "$DEVICE_FILE" | awk '{print $1}'
  fi
}

persist_rdc_state() {
  RDC_STATE_FILE="$DEVICE_FILE" RDC_STATE_OWNER="$RDC_AGENT_USER" ./scripts/persist-rdc-state.sh
}

runtime_value() {
  local key="$1"
  ./scripts/agent-runtime.sh status | awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}'
}

queue_successor() {
  if [[ "$SUCCESSOR_QUEUED" == "true" ]]; then return 0; fi
  if ! ./scripts/stop-tailscale.sh; then
    echo "::warning::Tailscale cleanup was not confirmed; refusing to pre-queue a successor."
    return 1
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
    echo "Draining Agent jobs and creating checkpoint before runner handoff."
    agent_run python3 scripts/lab_jobs.py drain --seconds "${AGENT_DRAIN_SECONDS:-900}" || echo "::warning::Job drain incomplete."
    env "${AGENT_SHARED_ENV[@]}" ./scripts/checkpoint-sync.sh save auto-pre-handoff || echo "::warning::Durable checkpoint failed; finalizer will retry."
    CHECKPOINT_DONE=true
  fi
}

if [[ "$(runtime_value RUNTIME_STATE)" == "UNKNOWN" ]]; then
  ./scripts/agent-runtime.sh start "$MINUTES" >/dev/null
fi

LAST_HASH="$(hash_state)"
echo "Keeping isolated RDC Lab online until the lifecycle enters the final auto-restart window."
./scripts/agent-runtime.sh status

TOTAL_TICKS=$((MINUTES * 6))
for ((tick=1; tick<=TOTAL_TICKS; tick++)); do
  sleep 10
  minute=$(((tick + 5) / 6))

  if agent_run ./scripts/health.sh >/dev/null 2>&1; then
    HEALTH_FAILURES=0
  else
    HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
  fi

  if (( HEALTH_FAILURES >= 3 )); then
    if [[ -s "$DEVICE_FILE" ]]; then
      persist_rdc_state || echo "::warning::RDC state persistence failed before restart."
      LAST_HASH="$(hash_state)"
    fi
    RESTARTS=$((RESTARTS + 1))
    echo "[$minute] RDC stopped; isolated Agent process restart $RESTARTS/$MAX_RESTARTS."
    if (( RESTARTS > MAX_RESTARTS )); then
      echo "RDC exceeded the local restart limit; ending this runner so the watchdog can replace it."
      exit 1
    fi
    agent_run ./scripts/stop-rdc.sh || true
    agent_run ./scripts/start-rdc.sh
    HEALTH_FAILURES=0
  fi

  now="$(date +%s)"
  if (( now - LAST_CHECKPOINT >= ${AGENT_CHECKPOINT_INTERVAL_SECONDS:-900} )); then
    env "${AGENT_SHARED_ENV[@]}" ./scripts/checkpoint-sync.sh save periodic || echo '::warning::Periodic durable checkpoint failed.'
    env "${AGENT_SHARED_ENV[@]}" timeout 90 python3 scripts/lab_incidents.py --flush || echo '::warning::Pending Lab incidents could not be delivered.'
    LAST_CHECKPOINT=$now
  fi

  CURRENT_HASH="$(hash_state)"
  if [[ -n "$CURRENT_HASH" && "$CURRENT_HASH" != "$LAST_HASH" ]]; then
    echo "[$minute] RDC session state changed; persisting encrypted identity through the trusted runner."
    persist_rdc_state
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
    PID="$(agent_pid)"
    RSS="$(ps -p "$PID" -o rss= 2>/dev/null | xargs || true)"
    echo "[$minute] RDC healthy pid=$PID rss_kb=${RSS:-unknown} lifecycle=${STATE:-UNKNOWN} elapsed=${ELAPSED:-?}m remaining=${REMAINING:-?}m."
    if [[ "$STATE" == "HANDOFF_DUE" ]] || { [[ "$REMAINING" =~ ^[0-9]+$ ]] && (( REMAINING <= AUTO_HANDOFF )); }; then
      echo "[$minute] Final ${AUTO_HANDOFF}m restart window reached; checkpointing and rotating to a fresh runner."
      create_checkpoint
      queue_successor
      break
    fi
  fi
done

echo "RDC Lab keepalive window completed; workflow finalizers will persist state and release the successor."
