#!/usr/bin/env bash
# Shared helpers for the disposable agent toolchain.

AGENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_TOOLSET_CLI="$AGENT_LIB_DIR/lab_toolset.py"

# ~/.local/bin holds manifest-installed shims such as the fd -> fdfind link
# created by agent-bootstrap.sh. Only login shells source ~/.profile, so
# non-interactive callers (SSH exec, cron, CI) would miss every command
# installed there and report a healthy toolchain as missing or outdated.
AGENT_LOCAL_BIN="${HOME}/.local/bin"
if [[ -d "$AGENT_LOCAL_BIN" ]]; then
  case ":$PATH:" in
    *":$AGENT_LOCAL_BIN:"*) ;;
    *) PATH="$AGENT_LOCAL_BIN:$PATH" ;;
  esac
  export PATH
fi

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

agent_required_full_pkg_config_modules() {
  python3 "$AGENT_TOOLSET_CLI" pkg-config-modules full
}

agent_pkg_config_path() {
  python3 "$AGENT_TOOLSET_CLI" pkg-config-path
}

agent_full_toolchain_ready() {
  local cmd module pkg_path
  while IFS= read -r cmd; do
    [[ -n "$cmd" ]] || continue
    command -v "$cmd" >/dev/null 2>&1 || return 1
  done < <(agent_required_full_commands)
  pkg_path="$(agent_pkg_config_path)"
  while IFS= read -r module; do
    [[ -n "$module" ]] || continue
    PKG_CONFIG_PATH="$pkg_path" pkg-config --exists "$module" >/dev/null 2>&1 || return 1
  done < <(agent_required_full_pkg_config_modules)
  return 0
}

agent_missing_full_commands() {
  local cmd module pkg_path
  while IFS= read -r cmd; do
    [[ -n "$cmd" ]] || continue
    command -v "$cmd" >/dev/null 2>&1 || printf '%s\n' "$cmd"
  done < <(agent_required_full_commands)
  pkg_path="$(agent_pkg_config_path)"
  while IFS= read -r module; do
    [[ -n "$module" ]] || continue
    PKG_CONFIG_PATH="$pkg_path" pkg-config --exists "$module" >/dev/null 2>&1 || printf 'pkg-config:%s\n' "$module"
  done < <(agent_required_full_pkg_config_modules)
}
