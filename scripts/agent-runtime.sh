#!/usr/bin/env bash
set -Eeuo pipefail

CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
RUNTIME_FILE="$CACHE_DIR/runtime.env"
REQUEST_FILE="$CACHE_DIR/handoff.request"
DEFAULT_AUTO_HANDOFF_MINUTES=20
mkdir -p "$CACHE_DIR"

write_start() {
  local minutes="$1"
  local auto_handoff="${AGENT_AUTO_HANDOFF_MINUTES:-$DEFAULT_AUTO_HANDOFF_MINUTES}"
  [[ "$minutes" =~ ^[0-9]+$ ]] || { echo "minutes must be an integer" >&2; exit 2; }
  [[ "$auto_handoff" =~ ^[0-9]+$ ]] || { echo "AGENT_AUTO_HANDOFF_MINUTES must be an integer" >&2; exit 2; }
  (( auto_handoff >= 1 && auto_handoff < minutes )) || {
    echo "AGENT_AUTO_HANDOFF_MINUTES must be between 1 and minutes-1" >&2
    exit 2
  }

  local now deadline restart_epoch
  now="$(date +%s)"
  deadline=$((now + minutes * 60))
  restart_epoch=$((deadline - auto_handoff * 60))
  rm -f "$REQUEST_FILE" "$CACHE_DIR/draining"
  {
    echo "RUN_ID=${GITHUB_RUN_ID:-unknown}"
    echo "RUN_ATTEMPT=${GITHUB_RUN_ATTEMPT:-unknown}"
    echo "START_EPOCH=$now"
    echo "HANDOFF_EPOCH=$deadline"
    echo "RESTART_EPOCH=$restart_epoch"
    echo "CHECKPOINT_EPOCH=$restart_epoch"
    echo "ONLINE_MINUTES=$minutes"
    echo "AUTO_HANDOFF_MINUTES=$auto_handoff"
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

  local now start deadline restart_epoch elapsed remaining auto_handoff mode message
  now="$(date +%s)"
  start="$(value START_EPOCH)"
  deadline="$(value HANDOFF_EPOCH)"
  auto_handoff="$(value AUTO_HANDOFF_MINUTES)"
  auto_handoff="${auto_handoff:-$DEFAULT_AUTO_HANDOFF_MINUTES}"
  restart_epoch="$(value RESTART_EPOCH)"
  restart_epoch="${restart_epoch:-$((deadline - auto_handoff * 60))}"
  elapsed=$(( (now - start) / 60 ))
  remaining=$(( (deadline - now + 59) / 60 ))
  ((remaining < 0)) && remaining=0

  if ((now >= deadline)); then
    mode="HANDOFF_DUE"
    message="The nominal handoff deadline has arrived. Stop using this runner."
  elif [[ -f "$REQUEST_FILE" ]]; then
    mode="RESTART_REQUESTED"
    message="A clean runner restart has been requested. Finish only the current atomic operation."
  elif ((remaining <= auto_handoff)); then
    mode="RESTART_WINDOW"
    message="Final restart window. Checkpoint and hand off to a fresh runner; do not start new heavy work."
  else
    mode="SAFE"
    message="Normal work window. Continue working; no early CAUTION mode is used."
  fi

  echo "RUNTIME_STATE=$mode"
  echo "RUN_ID=$(value RUN_ID)"
  echo "RUN_ATTEMPT=$(value RUN_ATTEMPT)"
  echo "ONLINE_MINUTES=$(value ONLINE_MINUTES)"
  echo "AUTO_HANDOFF_MINUTES=$auto_handoff"
  echo "ELAPSED_MINUTES=$elapsed"
  echo "REMAINING_MINUTES=$remaining"
  echo "START_UTC=$(date -u -d "@$start" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "HANDOFF_UTC=$(date -u -d "@$deadline" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "RESTART_UTC=$(date -u -d "@$restart_epoch" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "CHECKPOINT_UTC=$(date -u -d "@$restart_epoch" +'%Y-%m-%dT%H:%M:%SZ')"
  echo "HANDOFF_REQUESTED=$([[ -f "$REQUEST_FILE" ]] && echo true || echo false)"
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
