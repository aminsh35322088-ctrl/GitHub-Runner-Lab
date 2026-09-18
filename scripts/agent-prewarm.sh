#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
STATUS_FILE="$CACHE_DIR/prewarm.env"
LOG_FILE="$CACHE_DIR/prewarm.log"
LOCK_FILE="$CACHE_DIR/prewarm.lock"
PID_FILE="$CACHE_DIR/prewarm.pid"
mkdir -p "$CACHE_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[agent-prewarm] Another prewarm is already active."
  exit 0
fi

started_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf '%s\n'   "STATUS=RUNNING"   "STARTED_AT=$started_at"   "FINISHED_AT="   "PROFILE=full"   "PID=$$" > "$STATUS_FILE"
printf '%s\n' "$$" > "$PID_FILE"

finish() {
  code=$?
  finished_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  if ((code == 0)); then state="READY"; else state="FAILED"; fi
  printf '%s\n'     "STATUS=$state"     "STARTED_AT=$started_at"     "FINISHED_AT=$finished_at"     "PROFILE=full"     "PID=$$"     "EXIT_CODE=$code" > "$STATUS_FILE"
  rm -f "$PID_FILE"
  if ((code == 0)); then
    echo "[agent-prewarm] Full toolchain is ready."
  else
    echo "[agent-prewarm] Failed with exit code $code. See $LOG_FILE." >&2
  fi
}
trap finish EXIT

{
  echo "[agent-prewarm] started=$started_at host=$(hostname)"
  "$SELF_DIR/agent-bootstrap.sh" --full
  echo "[agent-prewarm] completed=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG_FILE" 2>&1
