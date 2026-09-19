#!/usr/bin/env bash
# Dedicated encrypted branch retaining exactly the latest authenticated archive.
set -Eeuo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_ROOT="$(cd "$SELF_DIR/.." && pwd)"
BRANCH=agent-checkpoints
CACHE="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
mkdir -p "$CACHE"
exec 8>"$CACHE/checkpoint-sync.lock"
flock -w 30 8
action="${1:-save}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
remote="$(git -C "$LAB_ROOT" remote get-url origin)"

if [[ "$action" == restore ]]; then
  if ! git -C "$LAB_ROOT" ls-remote --exit-code origin "refs/heads/$BRANCH" >/dev/null; then
    echo 'No durable checkpoint branch yet.'; exit 0
  fi
  git -C "$LAB_ROOT" fetch --quiet origin "$BRANCH"
  git -C "$LAB_ROOT" show FETCH_HEAD:latest.enc > "$tmp/latest.enc"
  destination="${AGENT_RECOVERY_DIR:-$HOME/agent-recovery}/${GITHUB_RUN_ID:-manual}-$(date +%s)"
  python3 "$SELF_DIR/lab_archive.py" decrypt "$tmp/latest.enc" "$destination"
  echo "RECOVERY_AVAILABLE=$destination/snapshot"
  exit 0
fi
[[ "$action" == save ]] || { echo 'Usage: checkpoint-sync.sh {save|restore}' >&2; exit 2; }
"$SELF_DIR/agent-checkpoint.sh" "${2:-periodic}"
"$SELF_DIR/package-agent-checkpoints.sh"
archive="${AGENT_CHECKPOINT_ARTIFACT_DIR:-${GITHUB_WORKSPACE:-$PWD}/.agent-artifacts}/latest.enc"
[[ -f "$archive" ]] || exit 1
git init --quiet "$tmp/repo"
git -C "$tmp/repo" remote add origin "$remote"
# Reuse checkout credentials without putting them in a URL or on stdout.
header="$(git -C "$LAB_ROOT" config --get http.https://github.com/.extraheader || true)"
if [[ -n "$header" ]]; then git -C "$tmp/repo" config http.https://github.com/.extraheader "$header"; fi
old="$(git -C "$tmp/repo" ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}')"
git -C "$tmp/repo" checkout --quiet --orphan "$BRANCH"
cp "$archive" "$tmp/repo/latest.enc"
git -C "$tmp/repo" add latest.enc
git -C "$tmp/repo" -c user.name='github-actions[bot]' -c user.email='41898282+github-actions[bot]@users.noreply.github.com' commit --quiet -m 'Save encrypted agent checkpoint'
if [[ -n "$old" ]]; then
  git -C "$tmp/repo" push --quiet --force-with-lease="refs/heads/$BRANCH:$old" origin "HEAD:refs/heads/$BRANCH"
else
  git -C "$tmp/repo" push --quiet origin "HEAD:refs/heads/$BRANCH"
fi
date -u +%FT%TZ > "$CACHE/checkpoint-persisted-at"
echo 'CHECKPOINT_DURABLE=true'
