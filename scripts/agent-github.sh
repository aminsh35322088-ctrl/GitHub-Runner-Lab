#!/usr/bin/env bash
set -Eeuo pipefail

HOST="${AGENT_GITHUB_HOST:-github.com}"

configured() {
  gh auth status --hostname "$HOST" >/dev/null 2>&1
}

case "${1:-status}" in
  install)
    # Read the repository secret from stdin exactly once. The caller should pipe
    # AGENT_GITHUB_TOKEN into this command; it is never persisted by this script
    # outside GitHub CLI's own authentication store.
    if [[ -t 0 ]]; then
      echo 'AGENT_GITHUB=NOT_CONFIGURED'
      echo 'agent-github.sh install expects the token on stdin' >&2
      exit 0
    fi
    if ! gh auth login --hostname "$HOST" --git-protocol https --with-token; then
      echo 'AGENT_GITHUB=FAILED' >&2
      exit 4
    fi
    gh auth setup-git --hostname "$HOST"
    if ! configured; then
      echo 'AGENT_GITHUB=FAILED' >&2
      exit 4
    fi
    echo 'AGENT_GITHUB=CONFIGURED'
    ;;
  status)
    if configured; then
      echo 'AGENT_GITHUB=CONFIGURED'
    else
      echo 'AGENT_GITHUB=NOT_CONFIGURED'
      exit 1
    fi
    ;;
  verify)
    configured || { echo 'AGENT_GITHUB=NOT_CONFIGURED' >&2; exit 4; }
    login="$(gh api user --jq .login)"
    [[ -n "$login" ]] || { echo 'AGENT_GITHUB_API=FAILED' >&2; exit 4; }
    if ! printf 'protocol=https\nhost=%s\n\n' "$HOST" |
      git credential fill |
      awk -F= '$1=="password" && length($2)>0 {ok=1} END {exit !ok}'; then
      echo 'AGENT_GITHUB_GIT_CREDENTIAL=FAILED' >&2
      exit 4
    fi
    echo 'AGENT_GITHUB=READY'
    echo "AGENT_GITHUB_LOGIN=$login"
    echo 'AGENT_GITHUB_API=READY'
    echo 'AGENT_GITHUB_GIT_CREDENTIAL=READY'
    ;;
  remove)
    if configured; then
      gh auth logout --hostname "$HOST" >/dev/null 2>&1 || true
    fi
    echo 'AGENT_GITHUB=REMOVED'
    ;;
  gh)
    shift
    exec gh "$@"
    ;;
  git|git-auto)
    shift
    exec git "$@"
    ;;
  *)
    echo 'Usage: agent-github.sh {install|status|verify|remove|gh ARGS...|git ARGS...|git-auto ARGS...}' >&2
    exit 2
    ;;
esac
