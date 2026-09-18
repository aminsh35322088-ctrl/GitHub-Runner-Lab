#!/usr/bin/env bash
# Shared helpers for the disposable agent toolchain.

agent_toolchain_version() {
  printf '%s\n' "2026-09-18.3"
}

agent_cache_dir() {
  printf '%s\n' "${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
}

agent_status_file() {
  printf '%s/prewarm.env\n' "$(agent_cache_dir)"
}

agent_log_file() {
  printf '%s/prewarm.log\n' "$(agent_cache_dir)"
}

agent_status_value() {
  local key="$1"
  local status_file
  status_file="$(agent_status_file)"
  [[ -f "$status_file" ]] || return 0
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$status_file"
}

agent_required_full_commands() {
  printf '%s\n'     git gh node npm python3 rg fd jq     cmake ninja gcc clang gdb git-lfs     ffmpeg convert sqlite3 shellcheck
}

agent_full_toolchain_ready() {
  local cmd
  while IFS= read -r cmd; do
    command -v "$cmd" >/dev/null 2>&1 || return 1
  done < <(agent_required_full_commands)
  return 0
}

agent_missing_full_commands() {
  local cmd
  while IFS= read -r cmd; do
    command -v "$cmd" >/dev/null 2>&1 || printf '%s\n' "$cmd"
  done < <(agent_required_full_commands)
}
