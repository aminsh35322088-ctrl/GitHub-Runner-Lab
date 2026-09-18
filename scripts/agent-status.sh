#!/usr/bin/env bash
set -Eeuo pipefail

CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
STATUS_FILE="$CACHE_DIR/prewarm.env"
LOG_FILE="$CACHE_DIR/prewarm.log"

value() {
  local key="$1"
  [[ -f "$STATUS_FILE" ]] || return 0
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$STATUS_FILE"
}

state="$(value STATUS)"
state="${state:-UNKNOWN}"
pid="$(value PID)"
if [[ "$state" == "RUNNING" && -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
  state="STALE"
fi

echo "AGENT_PREWARM=$state"
echo "STARTED_AT=$(value STARTED_AT)"
echo "FINISHED_AT=$(value FINISHED_AT)"
echo "PROFILE=$(value PROFILE)"
echo "PID=${pid:-}"

for pair in   "git:git" "gh:gh" "node:node" "npm:npm" "python:python3"   "rg:rg" "fd:fd" "jq:jq" "cmake:cmake" "ninja:ninja"   "gcc:gcc" "clang:clang" "gdb:gdb" "git_lfs:git-lfs"   "docker:docker" "ffmpeg:ffmpeg" "imagemagick:convert" "sqlite:sqlite3"; do
  name="${pair%%:*}"
  cmd="${pair#*:}"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '%s=READY\n' "${name^^}"
  else
    printf '%s=MISSING\n' "${name^^}"
  fi
done

if [[ "$state" == "FAILED" || "$state" == "STALE" ]]; then
  echo "--- prewarm log tail ---"
  tail -n 40 "$LOG_FILE" 2>/dev/null || true
fi
