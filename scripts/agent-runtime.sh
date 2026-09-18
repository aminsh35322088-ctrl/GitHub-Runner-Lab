#!/usr/bin/env bash
set -Eeuo pipefail

CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
RUNTIME_FILE="$CACHE_DIR/runtime.env"
mkdir -p "$CACHE_DIR"

write_start() {
  local minutes="$1"
  [[ "$minutes" =~ ^[0-9]+$ ]] || { echo "minutes must be an integer" >&2; exit 2; }
  local now deadline checkpoint
  now="$(date +%s)"
  deadline=$((now + minutes * 60))
  checkpoint=$((deadline - 30 * 60))
  {
    echo "RUN_ID=${GITHUB_RUN_ID:-unknown}"
    echo "RUN_ATTEMPT=${GITHUB_RUN_ATTEMPT:-unknown}"
    echo "START_EPOCH=$now"
    echo "HANDOFF_EPOCH=$deadline"
    echo "CHECKPOINT_EPOCH=$checkpoint"
    echo "ONLINE_MINUTES=$minutes"
    echo "RUN_URL=https://github.com/${GITHUB_REPOSITORY:-unknown}/actions/runs/${GITHUB_RUN_ID:-unknown}"
  } > "$RUNTIME_FILE"
}

value() {
  local key="$1"
  [[ -f "$RUNTIME_FILE" ]] || return 0
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$RUNTIME_FILE"
}

show_status() {
  if [[ ! -f "$RUNTIME_FILE" ]]; then
    echo "RUNTIME_STATE=UNKNOWN"
    echo "RUNTIME_MESSAGE=No local runtime timer has been initialized yet."
    return 0
  fi

  local now start deadline checkpoint elapsed remaining mode message
  now="$(date +%s)"
  start="$(value START_EPOCH)"
  deadline="$(value HANDOFF_EPOCH)"
  checkpoint="$(value CHECKPOINT_EPOCH)"
  elapsed=$(( (now - start) / 60 ))
  remaining=$(( (deadline - now + 59) / 60 ))
  ((remaining < 0)) && remaining=0

  if ((now >= deadline)); then
    mode="HANDOFF_DUE"
    message="Stop work now. The nominal handoff deadline has arrived."
  elif ((remaining <= 15)); then
    mode="HANDOFF_IMMINENT"
    message="Do not start or continue heavy work. Push durable changes and verify a checkpoint immediately."
  elif ((now >= checkpoint)); then
    mode="CHECKPOINT_REQUIRED"
    message="Stop starting heavy work. Push durable changes and create/verify a checkpoint."
  elif ((remaining <= 60)); then
    mode="CAUTION"
    message="Finish bounded work only. Prepare commits and avoid long operations."
  else
    mode="SAFE"
    message="Normal work window."
  fi

  echo "RUNTIME_STATE=$mode"
  echo "RUN_ID=$(value RUN_ID)"
  echo "RUN_ATTEMPT=$(value RUN_ATTEMPT)"
  echo "ONLINE_MINUTES=$(value ONLINE_MINUTES)"
  echo "ELAPSED_MINUTES=$elapsed"
  echo "REMAINING_MINUTES=$remaining"
  echo "START_UTC=$(date -u -d "@$start" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "HANDOFF_UTC=$(date -u -d "@$deadline" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "CHECKPOINT_UTC=$(date -u -d "@$checkpoint" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "RUN_URL=$(value RUN_URL)"
  echo "RUNTIME_MESSAGE=$message"
}

case "${1:-status}" in
  start)
    write_start "${2:-330}"
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: agent-runtime.sh {start [minutes]|status}" >&2
    exit 2
    ;;
esac
