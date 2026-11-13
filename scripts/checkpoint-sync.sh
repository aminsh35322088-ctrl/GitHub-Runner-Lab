#!/usr/bin/env bash
# Failure-only encrypted Git branch fallback retaining the latest authenticated archive.
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

repo_tag="$(printf '%s' "${GITHUB_REPOSITORY:-local}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9-' '-')"
RESTIC_RESTORE_STATE=unconfigured
restic_ready() {
  command -v restic >/dev/null 2>&1 &&
    [[ -n "${RESTIC_REPOSITORY:-}" && -n "${RESTIC_PASSWORD:-}" ]]
}
restic_snapshots() {
  restic snapshots --json --tag agent-checkpoint --tag "repo-${repo_tag}"
}
latest_restic_snapshot() {
  python3 -c 'import json,sys; rows=json.load(sys.stdin); rows.sort(key=lambda x:x.get("time","")); print(rows[-1]["id"] if rows else "")'
}
restore_snapshot_dir() {
  local snapshot="$1" source="$2" recovered
  recovered="${AGENT_RECOVERED_WORKSPACE_ROOT:-$HOME/agent-workspaces/recovered-${GITHUB_RUN_ID:-manual}-$(date +%s)}"
  python3 "$SELF_DIR/lab_checkpoint.py" resume "$snapshot" "$recovered"
  rm -f "$blocked"
  {
    echo "RECOVERY_STATUS=READY"
    echo "RECOVERY_SOURCE=$snapshot"
    echo "RECOVERY_BACKEND=$source"
    echo "RECOVERED_ROOT=$recovered"
  } > "$CACHE/recovery.env"
  chmod 600 "$CACHE/recovery.env"
  echo "RECOVERY_AVAILABLE=$snapshot"
  echo "RECOVERY_BACKEND=$source"
  echo "RECOVERED_ROOT=$recovered"
}
restore_archive() {
  local archive="$1" source="$2" destination
  destination="${AGENT_RECOVERY_DIR:-$HOME/agent-recovery}/${GITHUB_RUN_ID:-manual}-$(date +%s)-${source}"
  python3 "$SELF_DIR/lab_archive.py" decrypt "$archive" "$destination"
  restore_snapshot_dir "$destination/snapshot" "$source"
}
restore_from_restic() {
  restic_ready || { RESTIC_RESTORE_STATE=unconfigured; echo 'Restic is not configured or installed.' >&2; return 1; }
  RESTIC_RESTORE_STATE=failed
  local snapshots snapshot_id restored snapshot
  if ! restic cat config >/dev/null 2>&1; then
    if restic init >/dev/null 2>&1; then
      RESTIC_RESTORE_STATE=empty
      echo 'Initialized the configured Restic repository; no prior snapshot is available.' >&2
      return 1
    fi
    return 1
  fi
  snapshots="$(restic_snapshots)" || return 1
  snapshot_id="$(printf '%s' "$snapshots" | latest_restic_snapshot)" || return 1
  [[ -n "$snapshot_id" ]] || { RESTIC_RESTORE_STATE=empty; echo 'No Restic checkpoint snapshot exists yet.' >&2; return 1; }
  restored="$tmp/restic-restore"
  mkdir -p "$restored"
  restic restore "$snapshot_id" --target "$restored" || return 1
  snapshot="$(find "$restored" -type f -name manifest.json -printf '%h\n' -quit)"
  [[ -n "$snapshot" && -f "$snapshot/sha256.json" ]] || { echo 'Restic snapshot did not contain a complete Lab checkpoint.' >&2; return 1; }
  restore_snapshot_dir "$snapshot" restic || return 1
  RESTIC_RESTORE_STATE=ready
}
save_to_restic() {
  restic_ready || { echo 'Restic is not configured or installed.' >&2; return 1; }
  if ! restic cat config >/dev/null 2>&1; then
    restic init || return 1
  fi
  local snapshot_dir snapshots snapshot_id
  snapshot_dir="$(readlink -f "${AGENT_CHECKPOINT_DIR:-$HOME/agent-checkpoints}/latest")"
  [[ -f "$snapshot_dir/manifest.json" && -f "$snapshot_dir/sha256.json" ]] || return 1
  restic backup --json --tag agent-checkpoint --tag "repo-${repo_tag}" --host "${RESTIC_HOST:-github-runner-lab}" "$snapshot_dir" || return 1
  restic forget --group-by host,tags --tag agent-checkpoint --tag "repo-${repo_tag}" \
    --keep-daily "${RESTIC_KEEP_DAILY:-7}" --keep-weekly "${RESTIC_KEEP_WEEKLY:-4}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-3}" --prune || return 1
  snapshots="$(restic_snapshots)" || return 1
  snapshot_id="$(printf '%s' "$snapshots" | latest_restic_snapshot)" || return 1
  [[ -n "$snapshot_id" ]] || { echo 'Restic did not expose a committed checkpoint snapshot.' >&2; return 1; }
  echo "RESTIC_SNAPSHOT_ID=$snapshot_id"
}

if [[ "$action" == restore ]]; then
  touch "$blocked"
  printf 'RECOVERY_STATUS=FAILED\nRECOVERY_MESSAGE=Durable checkpoint restore has not completed; saving is blocked.\n' > "$CACHE/recovery.env"
  if restore_from_restic; then
    exit 0
  fi
  echo "Restic restore state: $RESTIC_RESTORE_STATE; trying the encrypted Git branch fallback." >&2
  ref_status=0
  git -C "$LAB_ROOT" ls-remote --exit-code origin "refs/heads/$BRANCH" >/dev/null || ref_status=$?
  case "$ref_status" in
    0) ;;
    2)
      if [[ "$RESTIC_RESTORE_STATE" == failed ]]; then
        echo 'Restic repository could not be read and no Git fallback exists; recovery is blocked.' >&2
        exit 1
      fi
      rm -f "$blocked"
      printf 'RECOVERY_STATUS=NONE\n' > "$CACHE/recovery.env"
      echo 'No durable checkpoint exists in Restic or the Git fallback branch.'; exit 0 ;;
    *) echo "Durable checkpoint lookup failed (git exit $ref_status); saving remains blocked." >&2
       exit "$ref_status" ;;
  esac
  git -C "$LAB_ROOT" fetch --quiet origin "$BRANCH"
  git -C "$LAB_ROOT" show FETCH_HEAD:latest.enc > "$tmp/latest.enc"
  restore_archive "$tmp/latest.enc" git-branch
  exit 0
fi
[[ "$action" == save ]] || { echo 'Usage: checkpoint-sync.sh {save|restore}' >&2; exit 2; }
if [[ -f "$blocked" ]]; then
  echo 'Durable checkpoint save blocked: previous recovery failed; repair it before overwriting the saved snapshot.' >&2
  exit 1
fi
"$SELF_DIR/agent-checkpoint.sh" "${2:-periodic}"
if save_to_restic; then
  date -u +%FT%TZ > "$CACHE/checkpoint-persisted-at"
  echo 'CHECKPOINT_DURABLE=true'
  echo 'CHECKPOINT_BACKEND=restic'
  exit 0
fi
echo 'Restic backup unavailable or unsuccessful; creating the encrypted Git branch fallback.' >&2
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
echo 'CHECKPOINT_BACKEND=git-branch'
