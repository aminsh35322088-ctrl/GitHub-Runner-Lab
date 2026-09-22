#!/usr/bin/env bash
set -Eeuo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  adopt|forget|list)
    action="$1"
    shift
    if [[ "$action" == "adopt" && $# -ge 1 ]]; then
      python3 "$SELF_DIR/lab_workspace_registry.py" "$action" "$@"
      python3 "$SELF_DIR/lab_git.py" identity "$1" || true
      exit 0
    fi
    exec python3 "$SELF_DIR/lab_workspace_registry.py" "$action" "$@"
    ;;
  *)
    exec python3 "$SELF_DIR/lab_workspace.py" "$@"
    ;;
esac
