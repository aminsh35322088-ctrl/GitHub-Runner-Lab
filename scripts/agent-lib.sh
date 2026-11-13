#!/usr/bin/env bash
# Shared helpers for the disposable agent toolchain.

AGENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_TOOLSET_CLI="$AGENT_LIB_DIR/lab_toolset.py"
RUNNER_TOOLS_ROOT="${RUNNER_TOOLS_ROOT:-/opt/runner-tools}"
AGENT_TOOL_INDEX_FILE="${AGENT_TOOL_INDEX_FILE:-$RUNNER_TOOLS_ROOT/registry/TOOL-INDEX.md}"
AGENT_TOOL_CATALOG="$AGENT_LIB_DIR/../config/runner-tools.json"
export RUNNER_TOOLS_ROOT AGENT_TOOL_INDEX_FILE AGENT_TOOL_CATALOG

# ~/.local/bin holds manifest-installed shims such as the fd -> fdfind link
# created by agent-bootstrap.sh. Only login shells source ~/.profile, so
# non-interactive callers (SSH exec, cron, CI) would miss every command
# installed there and report a healthy toolchain as missing or outdated.
AGENT_LOCAL_BIN="${HOME}/.local/bin"
for tool_bin in "$RUNNER_TOOLS_ROOT/bin" "$AGENT_LOCAL_BIN"; do
  if [[ -d "$tool_bin" ]]; then
    case ":$PATH:" in *":$tool_bin:"*) ;; *) PATH="$tool_bin:$PATH" ;; esac
  fi
done
export PATH
if [[ -d "$AGENT_LOCAL_BIN" ]]; then
  case ":$PATH:" in
    *":$AGENT_LOCAL_BIN:"*) ;;
    *) PATH="$AGENT_LOCAL_BIN:$PATH" ;;
  esac
  export PATH
fi

# REA is installed once per runner generation by the shared background
# prewarm. Source its generated environment here as well as from shell startup
# files so SSH exec and other non-login/non-interactive agent shells see it.
REA_ENV_FILE="${REA_INSTALL_ROOT:-$RUNNER_TOOLS_ROOT/tools/reverse-engineering/rea}/providers.env"
if [[ ! -r "$REA_ENV_FILE" && -r "${XDG_DATA_HOME:-$HOME/.local/share}/rea/providers.env" ]]; then
  REA_ENV_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/rea/providers.env"
fi
if [[ -r "$REA_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$REA_ENV_FILE"
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
