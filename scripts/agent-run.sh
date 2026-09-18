#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:-prepare}"
[[ $# -gt 0 ]] && shift || true

case "$cmd" in
  bootstrap)
    exec "$SELF_DIR/agent-bootstrap.sh" "$@"
    ;;
  doctor)
    exec "$SELF_DIR/agent-doctor.sh" "${1:-$PWD}"
    ;;
  workspace)
    exec "$SELF_DIR/agent-workspace.sh" "$@"
    ;;
  prepare)
    "$SELF_DIR/agent-bootstrap.sh"
    output="$("$SELF_DIR/agent-workspace.sh" "$@")"
    printf '%s\n' "$output"
    workspace="$(printf '%s\n' "$output" | sed -n 's/^AGENT_WORKSPACE=//p' | tail -n1)"
    [[ -n "$workspace" ]] || { echo "Could not resolve prepared workspace." >&2; exit 4; }
    "$SELF_DIR/agent-doctor.sh" "$workspace"
    ;;
  *)
    echo "Usage: agent-run.sh {prepare|bootstrap|workspace|doctor} [args...]" >&2
    exit 2
    ;;
esac
