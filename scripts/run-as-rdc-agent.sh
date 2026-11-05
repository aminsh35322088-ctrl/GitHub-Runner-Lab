#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo 'run-as-rdc-agent.sh must be called by the trusted runner, not root.' >&2
  exit 2
fi
RDC_AGENT_USER="${RDC_AGENT_USER:-rdc-agent}"
RDC_AGENT_HOME="${RDC_AGENT_HOME:-$(getent passwd "$RDC_AGENT_USER" | cut -d: -f6)}"
AGENT_LAB="$RDC_AGENT_HOME/lab"
[[ -n "$RDC_AGENT_HOME" && -d "$RDC_AGENT_HOME" && -d "$AGENT_LAB" ]] || { echo 'RDC Agent account/source snapshot is not provisioned.' >&2; exit 3; }
[[ "$#" -gt 0 ]] || { echo 'Usage: run-as-rdc-agent.sh COMMAND [ARGS...]' >&2; exit 2; }

AGENT_NODE_HOME="$RDC_AGENT_HOME/.node-runtime"
AGENT_PATH="$RDC_AGENT_HOME/.local/bin:$AGENT_NODE_HOME/bin:/usr/local/bin:/usr/bin:/bin"

# Environment allowlist. In particular, never inherit OIDC requester values,
# Runner Vault Session material, Agent GitHub PATs, or RDC_STATE_KEY.
AGENT_EXTRA_ENV=()
if [[ -n "${AGENT_GITHUB_DOCKER_HELPER:-}" ]]; then
  AGENT_EXTRA_ENV+=("AGENT_GITHUB_DOCKER_HELPER=$AGENT_GITHUB_DOCKER_HELPER")
fi
exec sudo -n -u "$RDC_AGENT_USER" -H -- env -i \
  HOME="$RDC_AGENT_HOME" USER="$RDC_AGENT_USER" LOGNAME="$RDC_AGENT_USER" \
  PATH="$AGENT_PATH" XDG_RUNTIME_DIR="$RDC_AGENT_HOME/.run" \
  RDC_PID_FILE="$RDC_AGENT_HOME/.run/rdc.pid" \
  RDC_LOG_FILE="$RDC_AGENT_HOME/.run/rdc.log" \
  RDC_HEALTH_FILE="$RDC_AGENT_HOME/.run/rdc-health.json" \
  RDC_BOOTSTRAP_LOG="$RDC_AGENT_HOME/.run/rdc-bootstrap.log" \
  NPM_CONFIG_PREFIX="$RDC_AGENT_HOME/.local" \
  AGENT_SYSTEM_PACKAGES_READY=true AGENT_CONTAINER_ENGINE=podman \
  AGENT_NPM_CACHE_DIR="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}" \
  AGENT_CHECKPOINT_ARTIFACT_DIR="${AGENT_CHECKPOINT_ARTIFACT_DIR:-/home/runner/agent-checkpoints/artifacts}" \
  AGENT_WORKSPACE_ROOT="${AGENT_WORKSPACE_ROOT:-/home/runner/agent-workspaces}" \
  AGENT_RECOVERY_DIR="${AGENT_RECOVERY_DIR:-/home/runner/agent-recovery}" \
  AGENT_JOBS_DIR="${AGENT_JOBS_DIR:-/home/runner/agent-jobs}" \
  AGENT_CHECKPOINT_DIR="${AGENT_CHECKPOINT_DIR:-/home/runner/agent-checkpoints}" \
  AGENT_KIT_CACHE_DIR="${AGENT_KIT_CACHE_DIR:-/home/runner/.cache/agent-runner-kit}" \
  AGENT_PROJECT_CACHE_ROOT="${AGENT_PROJECT_CACHE_ROOT:-/home/runner/.cache/agent-projects}" \
  npm_config_cache="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}" \
  GITHUB_WORKSPACE="$AGENT_LAB" \
  GITHUB_RUN_ID="${GITHUB_RUN_ID:-manual}" \
  GITHUB_RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}" \
  GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}" \
  GITHUB_REF="${GITHUB_REF:-}" GITHUB_SHA="${GITHUB_SHA:-}" \
  CI=true LANG="${LANG:-C.UTF-8}" \
  "${AGENT_EXTRA_ENV[@]}" bash -c 'cd "$1" && shift && exec "$@"' _ "$AGENT_LAB" "$@"
