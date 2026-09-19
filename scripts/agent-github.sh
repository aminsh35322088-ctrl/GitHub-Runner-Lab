#!/usr/bin/env bash
set -Eeuo pipefail
TOKEN_FILE="${AGENT_GITHUB_TOKEN_FILE:-/run/agent-lab/github-token}"
case "${1:-status}" in
  install)
    # Invoked only by a workflow step carrying the repository secret.
    if [[ -z "${AGENT_GITHUB_TOKEN:-}" ]]; then
      echo 'AGENT_GITHUB=NOT_CONFIGURED'
      exit 0
    fi
    sudo install -d -m 700 /run/agent-lab
    printf '%s' "$AGENT_GITHUB_TOKEN" | sudo tee "$TOKEN_FILE" >/dev/null
    sudo chmod 600 "$TOKEN_FILE"
    echo 'AGENT_GITHUB=CONFIGURED'
    ;;
  status)
    if sudo test -s "$TOKEN_FILE"; then
      echo 'AGENT_GITHUB=CONFIGURED'
    else
      echo 'AGENT_GITHUB=NOT_CONFIGURED'
    fi
    ;;
  gh|git)
    command="$1"; shift
    export GH_TOKEN
    GH_TOKEN="$(sudo cat "$TOKEN_FILE")"
    [[ -n "$GH_TOKEN" ]] || { echo 'AGENT_GITHUB_TOKEN is unavailable' >&2; exit 4; }
    export GH_PROMPT_DISABLED=1
    if [[ "$command" == gh ]]; then
      exec gh "$@"
    fi
    # Command-scoped helper; clear persisted checkout headers and other helpers.
    exec git -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
      -c http.https://github.com/.extraheader= "$@"
    ;;
  *) echo 'Usage: agent-github.sh {install|status|gh ARGS...|git ARGS...}' >&2; exit 2 ;;
esac
