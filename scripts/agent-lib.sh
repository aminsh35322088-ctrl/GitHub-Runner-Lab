#!/usr/bin/env bash
# Shared constants/helpers for the disposable agent toolchain.

AGENT_TOOLCHAIN_VERSION="2026-09-18.2"
AGENT_CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
AGENT_STATUS_FILE="$AGENT_CACHE_DIR/prewarm.env"
AGENT_LOG_FILE="$AGENT_CACHE_DIR/prewarm.log"

AGENT_REQUIRED_FULL_COMMANDS=(
  git gh node npm python3 rg fd jq
  cmake ninja gcc clang gdb git-lfs
  ffmpeg convert sqlite3 shellcheck
)

agent_status_value() {
  local key="$1"
  [[ -f "$AGENT_STATUS_FILE" ]] || return 0
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$AGENT_STATUS_FILE"
}

agent_full_toolchain_ready() {
  local cmd
  for cmd in "${AGENT_REQUIRED_FULL_COMMANDS[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || return 1
  done
  return 0
}

agent_missing_full_commands() {
  local cmd
  for cmd in "${AGENT_REQUIRED_FULL_COMMANDS[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || printf '%s\n' "$cmd"
  done
}
