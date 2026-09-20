#!/usr/bin/env bash
# Shared helpers for the disposable agent toolchain.

AGENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_TOOLSET_CLI="$AGENT_LIB_DIR/lab_toolset.py"

agent_toolchain_version() {
  python3 "$AGENT_TOOLSET_CLI" toolchain-version
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
  python3 "$AGENT_TOOLSET_CLI" commands full
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
