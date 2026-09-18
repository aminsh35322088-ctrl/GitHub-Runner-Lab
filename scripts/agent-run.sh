#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:-prepare}"
[[ $# -gt 0 ]] && shift || true

case "$cmd" in
  bootstrap) exec "$SELF_DIR/agent-bootstrap.sh" "$@" ;;
  prewarm) exec "$SELF_DIR/agent-prewarm.sh" "$@" ;;
  status) exec "$SELF_DIR/agent-status.sh" ;;
  doctor) exec "$SELF_DIR/agent-doctor.sh" "${1:-$PWD}" ;;
  workspace) exec "$SELF_DIR/agent-workspace.sh" "$@" ;;
  prepare)
    profile=""
    workspace_args=()
    while (($#)); do
      case "$1" in
        --core|--build|--media|--full) profile="$1"; shift ;;
        --repo|--ref|--pr|--dir) workspace_args+=("$1" "$2"); shift 2 ;;
        --deps|--force) workspace_args+=("$1"); shift ;;
        -h|--help)
          echo "Usage: agent-run.sh prepare [--core|--build|--media|--full] [workspace options]"
          exit 0
          ;;
        *) echo "Unknown prepare option: $1" >&2; exit 2 ;;
      esac
    done

    if [[ -n "$profile" ]]; then
      "$SELF_DIR/agent-bootstrap.sh" "$profile"
    elif ! command -v git >/dev/null 2>&1 || ! command -v rg >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
      "$SELF_DIR/agent-bootstrap.sh" --core
    fi

    output="$("$SELF_DIR/agent-workspace.sh" "${workspace_args[@]}")"
    printf '%s\n' "$output"
    workspace="$(printf '%s\n' "$output" | sed -n 's/^AGENT_WORKSPACE=//p' | tail -n1)"
    [[ -n "$workspace" ]] || { echo "Could not resolve prepared workspace." >&2; exit 4; }
    "$SELF_DIR/agent-doctor.sh" "$workspace"
    ;;
  *)
    echo "Usage: agent-run.sh {prepare|bootstrap|prewarm|status|workspace|doctor} [args...]" >&2
    exit 2
    ;;
esac
