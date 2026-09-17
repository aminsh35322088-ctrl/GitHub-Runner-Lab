#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"

if (( ${#RDC_STATE_KEY} < 32 )); then
  echo "RDC_STATE_KEY must be at least 32 characters long."
  exit 2
fi

DEVICE_FILE="$HOME/.desktop-commander-device/device.json"
STATE_BRANCH="${RDC_STATE_BRANCH:-rdc-state}"
STATE_PATH=".rdc-state/device.json.enc"
ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
WT="$TMP/worktree"
ENC="$TMP/device.json.enc"

valid_state() {
  node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.access_token||!j.session?.refresh_token)process.exit(2)" "$1" >/dev/null 2>&1
}

[[ -s "$DEVICE_FILE" ]] || { echo "No RDC device state to persist."; exit 2; }
valid_state "$DEVICE_FILE" || { echo "RDC device state is invalid; refusing to persist it."; exit 2; }

openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
  -in "$DEVICE_FILE" -out "$ENC" -pass env:RDC_STATE_KEY

cleanup() {
  cd "$ROOT" 2>/dev/null || true
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

cd "$ROOT"
if git fetch --quiet origin "refs/heads/${STATE_BRANCH}:refs/remotes/origin/${STATE_BRANCH}" 2>/dev/null; then
  git worktree add --quiet --detach "$WT" "refs/remotes/origin/${STATE_BRANCH}"
  cd "$WT"
  git switch --quiet -C "$STATE_BRANCH"
else
  git worktree add --quiet --detach "$WT" HEAD
  cd "$WT"
  git switch --quiet --orphan "$STATE_BRANCH"
  git rm -rf . >/dev/null 2>&1 || true
fi

mkdir -p "$(dirname "$STATE_PATH")"
cp "$ENC" "$STATE_PATH"
git add -f "$STATE_PATH"

if git diff --cached --quiet; then
  echo "Encrypted RDC state is already current."
  exit 0
fi

git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit --quiet -m "Update encrypted RDC state"

git push --quiet origin "HEAD:refs/heads/${STATE_BRANCH}"
echo "Encrypted RDC state persisted to ${STATE_BRANCH}:${STATE_PATH}."
