#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:-prepare}"
if (($# > 0)); then
  shift
fi

case "$cmd" in
  job) exec python3 "$SELF_DIR/lab_jobs.py" "$@" ;;
  resume) exec python3 "$SELF_DIR/lab_checkpoint.py" resume "$@" ;;
  decrypt) exec python3 "$SELF_DIR/lab_archive.py" decrypt "$@" ;;
  github) exec "$SELF_DIR/agent-github.sh" "$@" ;;
  ready) exec python3 "$SELF_DIR/lab_ready.py" ;;
  cache) exec python3 "$SELF_DIR/lab_cache.py" "$@" ;;
  selftest) exec python3 -m unittest discover -s "$SELF_DIR/../tests" -v ;;
  validate) exec "$SELF_DIR/agent-project.sh" validate "${1:-$PWD}" "${@:2}" ;;
  shell-help)
    echo "Use agent-run.sh github ... for authenticated GitHub operations."
    echo "Use agent-run.sh validate WORKSPACE for managed full validation; do not run raw npm ci against shared node_modules links."
    ;;

  bootstrap) exec "$SELF_DIR/agent-bootstrap.sh" "$@" ;;
  prewarm) exec "$SELF_DIR/agent-prewarm.sh" "$@" ;;
  status) exec "$SELF_DIR/agent-status.sh" ;;
  runtime) exec "$SELF_DIR/agent-runtime.sh" "$@" ;;
  restart|handoff) exec "$SELF_DIR/agent-handoff.sh" "$@" ;;
  checkpoint) exec "$SELF_DIR/agent-checkpoint.sh" "${1:-manual}" ;;
  doctor) exec "$SELF_DIR/agent-doctor.sh" "${1:-$PWD}" ;;
  workspace) exec "$SELF_DIR/agent-workspace.sh" "$@" ;;
  project) exec "$SELF_DIR/agent-project.sh" "$@" ;;
  work)
    profile=""
    workspace_args=()
    tests=()
    run_check=false
    while (($#)); do
      case "$1" in
        --core|--build|--media|--full) profile="$1"; shift ;;
        --repo|--ref|--pr|--dir) workspace_args+=("$1" "$2"); shift 2 ;;
        --test) tests+=("$2"); shift 2 ;;
        --check) run_check=true; shift ;;
        -h|--help)
          echo "Usage: agent-run.sh work [workspace options] [--test SELECTOR ...] [--check]"
          exit 0
          ;;
        *) echo "Unknown work option: $1" >&2; exit 2 ;;
      esac
    done

    runtime="$("$SELF_DIR/agent-runtime.sh" status)"
    state="$(awk -F= '$1=="RUNTIME_STATE"{print $2}' <<<"$runtime")"
    if [[ "$state" != "SAFE" && "$state" != "UNKNOWN" ]]; then
      printf '%s
' "$runtime"
      "$SELF_DIR/agent-handoff.sh" || true
      echo "Runner is in its restart window; refusing to start new project work." >&2
      exit 75
    fi

    if [[ -n "$profile" ]]; then
      "$SELF_DIR/agent-bootstrap.sh" "$profile"
    elif ! command -v git >/dev/null 2>&1 || ! command -v rg >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
      "$SELF_DIR/agent-bootstrap.sh" --core
    fi

    output="$("$SELF_DIR/agent-workspace.sh" "${workspace_args[@]}")"
    printf '%s
' "$output"
    workspace="$(printf '%s
' "$output" | sed -n 's/^AGENT_WORKSPACE=//p' | tail -n1)"
    [[ -n "$workspace" ]] || { echo "Could not resolve prepared workspace." >&2; exit 4; }

    "$SELF_DIR/agent-project.sh" prepare "$workspace"
    "$SELF_DIR/agent-doctor.sh" "$workspace"
    if [[ "$run_check" == "true" ]]; then
      "$SELF_DIR/agent-project.sh" check "$workspace"
    fi
    if (("${#tests[@]}" > 0)); then
      "$SELF_DIR/agent-project.sh" test "$workspace" "${tests[@]}"
    fi
    ;;
  prepare)
    profile=""
    workspace_args=()
    while (($#)); do
      case "$1" in
        --core|--build|--media|--full) profile="$1"; shift ;;
        --repo|--ref|--pr|--dir) workspace_args+=("$1" "$2"); shift 2 ;;
        --deps) workspace_args+=("$1"); shift ;;
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
    printf '%s
' "$output"
    workspace="$(printf '%s
' "$output" | sed -n 's/^AGENT_WORKSPACE=//p' | tail -n1)"
    [[ -n "$workspace" ]] || { echo "Could not resolve prepared workspace." >&2; exit 4; }
    "$SELF_DIR/agent-doctor.sh" "$workspace"
    ;;
  *)
    echo "Usage: agent-run.sh {work|prepare|validate|shell-help|job|resume|decrypt|github|ready|cache|bootstrap|prewarm|status|runtime|restart|checkpoint|workspace|project|doctor|selftest} [args...]" >&2
    exit 2
    ;;
esac
