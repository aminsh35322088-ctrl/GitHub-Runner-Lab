#!/usr/bin/env bash
set -Eeuo pipefail

SELF="$(readlink -f "$0")"
SELF_DIR="$(dirname "$SELF")"
CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
STATUS_FILE="$CACHE_DIR/prewarm.env"
LOG_FILE="$CACHE_DIR/prewarm.log"
LOCK_FILE="$CACHE_DIR/prewarm.lock"
mkdir -p "$CACHE_DIR"

read_state() {
  local key="$1"
  [[ -f "$STATUS_FILE" ]] || return 0
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$STATUS_FILE"
}

if [[ "${1:-}" == "--background" ]]; then
  state="$(read_state STATUS)"
  pid="$(read_state PID)"
  if [[ "$state" == "READY" ]]; then
    echo "[agent-prewarm] Full toolchain is already ready."
    exit 0
  fi
  if [[ "$state" == "RUNNING" && -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[agent-prewarm] Already running pid=$pid."
    exit 0
  fi
  nohup "$SELF" --foreground </dev/null >/dev/null 2>&1 &
  echo "[agent-prewarm] Background prewarm started pid=$!."
  exit 0
fi

if [[ -n "${1:-}" && "${1:-}" != "--foreground" ]]; then
  echo "Usage: agent-prewarm.sh [--background|--foreground]" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[agent-prewarm] Another prewarm is already active."
  exit 0
fi

started_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
write_status() {
  local state="$1" finished_at="${2:-}" exit_code="${3:-}"
  {
    echo "STATUS=$state"
    echo "STARTED_AT=$started_at"
    echo "FINISHED_AT=$finished_at"
    echo "PROFILE=full"
    echo "PID=$$"
    [[ -n "$exit_code" ]] && echo "EXIT_CODE=$exit_code"
  } > "$STATUS_FILE"
}

write_status RUNNING

finish() {
  code=$?
  if ((code == 0)); then state="READY"; else state="FAILED"; fi
  write_status "$state" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$code"
  if ((code != 0)); then
    echo "[agent-prewarm] Failed with exit code $code. See $LOG_FILE." >&2
  fi
}
trap finish EXIT

{
  echo "[agent-prewarm] started=$started_at host=$(hostname)"
  "$SELF_DIR/agent-bootstrap.sh" --full
  echo "[agent-prewarm] completed=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG_FILE" 2>&1
