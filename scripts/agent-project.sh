#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-status}"
shift || true
TARGET="${1:-$PWD}"
if (($# > 0)); then shift; fi

TARGET="$(cd "$TARGET" && pwd)"
CONFIG_REL="${AGENT_PROJECT_RUNNER:-.github/agent-lab/runner.sh}"
CONFIG="$TARGET/$CONFIG_REL"
CACHE_ROOT="${AGENT_PROJECT_CACHE_ROOT:-$HOME/.cache/agent-projects}"

remote="$(git -C "$TARGET" remote get-url origin 2>/dev/null || printf '%s' "$TARGET")"
repo_key="$(printf '%s' "$remote" | sha256sum | cut -c1-20)"
export AGENT_PROJECT_CACHE_DIR="$CACHE_ROOT/$repo_key"
mkdir -p "$AGENT_PROJECT_CACHE_DIR"

if [[ "$ACTION" == "status" ]]; then
  echo "AGENT_PROJECT_ROOT=$TARGET"
  echo "AGENT_PROJECT_RUNNER=$CONFIG"
  echo "AGENT_PROJECT_CACHE_DIR=$AGENT_PROJECT_CACHE_DIR"
  if [[ -f "$CONFIG" ]]; then
    echo "AGENT_PROJECT_CONFIG=READY"
  else
    echo "AGENT_PROJECT_CONFIG=MISSING"
  fi
  exit 0
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "Target branch does not provide $CONFIG_REL." >&2
  echo "Project-specific setup/test policy belongs in the target repository, not GitHub-Runner-Lab." >&2
  exit 4
fi

export AGENT_PROJECT_ROOT="$TARGET"
exec bash "$CONFIG" "$ACTION" "$@"
