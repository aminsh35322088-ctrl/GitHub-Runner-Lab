#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-status}"
shift || true
TARGET="${1:-$PWD}"
if (($# > 0)); then shift; fi

TARGET="$(cd "$TARGET" && pwd)"
CONFIG_REL="${AGENT_PROJECT_RUNNER:-.github/agent-lab/runner.sh}"
CONFIG="$TARGET/$CONFIG_REL"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRACT_TOOL="$SELF_DIR/lab_project_contract.py"
CACHE_ROOT="${AGENT_PROJECT_CACHE_ROOT:-$HOME/.cache/agent-projects}"
export AGENT_PROJECT_CACHE_ROOT="$CACHE_ROOT"

remote="$(git -C "$TARGET" remote get-url origin 2>/dev/null || printf '%s' "$TARGET")"
runtime_key="$(printf '%s\n' "$(uname -sm)" "$(node --version)" | sha256sum | cut -c1-12)"
repo_key="$(printf '%s' "$remote" | sha256sum | cut -c1-20)"
export AGENT_PROJECT_CACHE_DIR="$CACHE_ROOT/$runtime_key/$repo_key"
mkdir -p "$AGENT_PROJECT_CACHE_DIR"

if [[ "$ACTION" == "status" ]]; then
  echo "AGENT_PROJECT_ROOT=$TARGET"
  echo "AGENT_PROJECT_RUNNER=$CONFIG"
  echo "AGENT_PROJECT_CACHE_DIR=$AGENT_PROJECT_CACHE_DIR"
  if [[ -f "$CONFIG" ]]; then
    echo "AGENT_PROJECT_CONFIG=READY"
    export AGENT_PROJECT_ROOT="$TARGET"
    exec bash "$CONFIG" status "$@"
  else
    echo "AGENT_PROJECT_CONFIG=MISSING"
    exit 0
  fi
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "Target branch does not provide $CONFIG_REL." >&2
  echo "Project-specific setup/test policy belongs in the target repository, not GitHub-Runner-Lab." >&2
  exit 4
fi

if [[ -z "${AGENT_JOB_ID:-}" ]]; then
  job_args=(start --cwd "$TARGET" --timeout "${AGENT_JOB_TIMEOUT:-1800}" --grace-seconds "${AGENT_JOB_GRACE_SECONDS:-10}" --pass-env AGENT_PROJECT_CACHE_ROOT)
  [[ -n "${AGENT_PROJECT_RUNNER:-}" ]] && job_args+=(--pass-env AGENT_PROJECT_RUNNER)
  [[ -n "${AGENT_PROJECT_CONTRACT:-}" ]] && job_args+=(--pass-env AGENT_PROJECT_CONTRACT)
  [[ -n "${AGENT_LEGACY_LOCK_WAIT:-}" ]] && job_args+=(--pass-env AGENT_LEGACY_LOCK_WAIT)
  [[ -n "${AGENT_VALIDATION_LOG_MAX_MB:-}" ]] && job_args+=(--pass-env AGENT_VALIDATION_LOG_MAX_MB)
  job="$(python3 "$SELF_DIR/lab_jobs.py" "${job_args[@]}" -- bash "$SELF_DIR/agent-project.sh" "$ACTION" "$TARGET" "$@")"
  echo "AGENT_JOB_ID=$job"
  exec python3 "$SELF_DIR/lab_jobs.py" wait "$job"
fi
export AGENT_PROJECT_ROOT="$TARGET"
if grep -q '/app/node_modules' "$CONFIG"; then
  # Compatibility for older project hooks. The global path is serialized until
  # that project moves to workspace-local dependency links.
  exec 7>"$CACHE_ROOT/legacy-app-node-modules.lock"
  flock -w "${AGENT_LEGACY_LOCK_WAIT:-900}" 7
fi

bootstrap_required="$(python3 "$CONTRACT_TOOL" bootstrap-required "$TARGET")"
project_bootstrap() {
  if [[ "$bootstrap_required" == "true" ]]; then
    bash "$CONFIG" bootstrap
  fi
  python3 "$CONTRACT_TOOL" check "$TARGET"
}

if [[ "$ACTION" == "bootstrap" ]]; then
  project_bootstrap
  exit 0
fi

if [[ "$ACTION" != "clean-materialized" ]]; then
  project_bootstrap
fi

if [[ "$ACTION" == "validate" ]]; then
  exec python3 "$SELF_DIR/lab_validate.py" "$TARGET" "$@"
fi
exec bash "$CONFIG" "$ACTION" "$@"
