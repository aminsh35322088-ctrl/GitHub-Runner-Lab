#!/usr/bin/env bash
set -Eeuo pipefail

SELF="$(readlink -f "$0")"
SELF_DIR="$(dirname "$SELF")"
# shellcheck source=agent-lib.sh
source "$SELF_DIR/agent-lib.sh"
LOCK_FILE="$AGENT_CACHE_DIR/prewarm.lock"
mkdir -p "$AGENT_CACHE_DIR"

if [[ "${1:-}" == "--background" ]]; then
  state="$(agent_status_value STATUS)"
  version="$(agent_status_value TOOLCHAIN_VERSION)"
  pid="$(agent_status_value PID)"

  if [[ "$state" == "READY" && "$version" == "$AGENT_TOOLCHAIN_VERSION" ]] && agent_full_toolchain_ready; then
    echo "[agent-prewarm] Full toolchain is already ready (version=$AGENT_TOOLCHAIN_VERSION)."
    exit 0
  fi
  if [[ "$state" == "RUNNING" && "$version" == "$AGENT_TOOLCHAIN_VERSION" && -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[agent-prewarm] Already running pid=$pid version=$version."
    exit 0
  fi

  nohup "$SELF" --foreground </dev/null >/dev/null 2>&1 &
  echo "[agent-prewarm] Background prewarm started pid=$! version=$AGENT_TOOLCHAIN_VERSION."
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
    echo "TOOLCHAIN_VERSION=$AGENT_TOOLCHAIN_VERSION"
    echo "STARTED_AT=$started_at"
    echo "FINISHED_AT=$finished_at"
    echo "PROFILE=full"
    echo "PID=$$"
    [[ -n "$exit_code" ]] && echo "EXIT_CODE=$exit_code"
  } > "$AGENT_STATUS_FILE"
}

write_status RUNNING

finish() {
  code=$?
  if ((code == 0)); then state="READY"; else state="FAILED"; fi
  write_status "$state" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$code"
  if ((code != 0)); then
    echo "[agent-prewarm] Failed with exit code $code. See $AGENT_LOG_FILE." >&2
  fi
}
trap finish EXIT

{
  echo "[agent-prewarm] started=$started_at host=$(hostname) version=$AGENT_TOOLCHAIN_VERSION"
  "$SELF_DIR/agent-bootstrap.sh" --full
  if ! agent_full_toolchain_ready; then
    echo "[agent-prewarm] Required commands still missing after bootstrap:"
    agent_missing_full_commands
    exit 20
  fi
  echo "[agent-prewarm] completed=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$AGENT_LOG_FILE" 2>&1
