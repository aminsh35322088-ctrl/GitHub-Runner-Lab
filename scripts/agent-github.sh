#!/usr/bin/env bash
set -Eeuo pipefail

HOST="${AGENT_GITHUB_HOST:-github.com}"

gh_config_dir() {
  printf '%s\n' "${GH_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gh}"
}

job_git_config() {
  printf '%s\n' "${AGENT_GITHUB_JOB_GIT_CONFIG:-$HOME/.config/agent-lab/github-auth/gitconfig}"
}

write_job_git_config() {
  local file
  file="$(job_git_config)"
  install -d -m 700 "$(dirname "$file")"
  : > "$file"
  chmod 600 "$file"
  git config --file "$file" "credential.https://$HOST.helper" ""
  git config --file "$file" --add "credential.https://$HOST.helper" '!gh auth git-credential'
}

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
    write_job_git_config
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
    job_config="$(job_git_config)"
    shared_gh_config="$(gh_config_dir)"
    [[ -s "$job_config" ]] || { echo 'AGENT_GITHUB_GIT_CONFIG=FAILED' >&2; exit 4; }
    if ! (
      export GH_CONFIG_DIR="$shared_gh_config"
      export GIT_CONFIG_GLOBAL="$job_config"
      export GIT_CONFIG_NOSYSTEM=1
      printf 'protocol=https\nhost=%s\n\n' "$HOST" |
        git credential fill |
        awk -F= '$1=="password" && length($2)>0 {ok=1} END {exit !ok}'
    ); then
      echo 'AGENT_GITHUB_GIT_CREDENTIAL=FAILED' >&2
      exit 4
    fi
    echo 'AGENT_GITHUB=READY'
    echo "AGENT_GITHUB_LOGIN=$login"
    echo 'AGENT_GITHUB_API=READY'
    echo 'AGENT_GITHUB_GIT_CREDENTIAL=READY'
    echo 'AGENT_GITHUB_MANAGED_JOB=READY'
    ;;
  remove)
    if configured; then
      gh auth logout --hostname "$HOST" >/dev/null 2>&1 || true
    fi
    rm -f "$(job_git_config)"
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
