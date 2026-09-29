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
blocked="$CACHE/recovery.blocked"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
remote="$(git -C "$LAB_ROOT" remote get-url origin)"

if [[ "$action" == restore ]]; then
  touch "$blocked"
  printf 'RECOVERY_STATUS=FAILED\nRECOVERY_MESSAGE=Durable checkpoint restore has not completed; saving is blocked.\n' > "$CACHE/recovery.env"
  ref_status=0
  git -C "$LAB_ROOT" ls-remote --exit-code origin "refs/heads/$BRANCH" >/dev/null || ref_status=$?
  case "$ref_status" in
    0) ;;
    2)
      rm -f "$blocked"
      printf 'RECOVERY_STATUS=NONE\n' > "$CACHE/recovery.env"
      echo 'No durable checkpoint branch yet.'; exit 0 ;;
    *) echo "Durable checkpoint lookup failed (git exit $ref_status); saving remains blocked." >&2
       exit "$ref_status" ;;
  esac
  git -C "$LAB_ROOT" fetch --quiet origin "$BRANCH"
  git -C "$LAB_ROOT" show FETCH_HEAD:latest.enc > "$tmp/latest.enc"
  destination="${AGENT_RECOVERY_DIR:-$HOME/agent-recovery}/${GITHUB_RUN_ID:-manual}-$(date +%s)"
  python3 "$SELF_DIR/lab_archive.py" decrypt "$tmp/latest.enc" "$destination"
  snapshot="$destination/snapshot"
  recovered="${AGENT_RECOVERED_WORKSPACE_ROOT:-$HOME/agent-workspaces/recovered-${GITHUB_RUN_ID:-manual}-$(date +%s)}"
  python3 "$SELF_DIR/lab_checkpoint.py" resume "$snapshot" "$recovered"
  rm -f "$blocked"
  {
    echo "RECOVERY_STATUS=READY"
    echo "RECOVERY_SOURCE=$snapshot"
    echo "RECOVERED_ROOT=$recovered"
  } > "$CACHE/recovery.env"
  chmod 600 "$CACHE/recovery.env"
  echo "RECOVERY_AVAILABLE=$snapshot"
  echo "RECOVERED_ROOT=$recovered"
  exit 0
fi
[[ "$action" == save ]] || { echo 'Usage: checkpoint-sync.sh {save|restore}' >&2; exit 2; }
if [[ -f "$blocked" ]]; then
  echo 'Durable checkpoint save blocked: previous recovery failed; repair it before overwriting the saved snapshot.' >&2
  exit 1
fi
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
