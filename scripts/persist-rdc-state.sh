#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"
if (( ${#RDC_STATE_KEY} < 32 )); then
  echo "RDC_STATE_KEY must be at least 32 characters long."
  exit 2
fi

DEVICE_FILE="${RDC_STATE_FILE:-$HOME/.desktop-commander-device/device.json}"
STATE_OWNER="${RDC_STATE_OWNER:-}"
STATE_BRANCH="${RDC_STATE_BRANCH:-rdc-state}"
STATE_PATH=".rdc-state/device.json.enc"
ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
WT="$TMP/worktree"
RAW_DEVICE="$TMP/device.json"
ENC="$TMP/device.json.enc"
chmod 0700 "$TMP"

cleanup() {
  cd "$ROOT" 2>/dev/null || true
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

# The trusted workflow runner owns Git checkout/push. The device identity is
# owned by rdc-agent; read it through the authorized UID into a private temp
# file so the Agent never receives the persistence key or GitHub write token.
if [[ -r "$DEVICE_FILE" ]]; then
  cp -- "$DEVICE_FILE" "$RAW_DEVICE"
elif [[ -n "$STATE_OWNER" ]] && command -v sudo >/dev/null 2>&1; then
  sudo -n -u "$STATE_OWNER" -- cat -- "$DEVICE_FILE" | tee "$RAW_DEVICE" >/dev/null
else
  echo "No RDC device state is readable for persistence." >&2
  exit 2
fi
chmod 0600 "$RAW_DEVICE"

valid_state() {
  node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.access_token||!j.session?.refresh_token)process.exit(2)" "$1" >/dev/null 2>&1
}
valid_state "$RAW_DEVICE" || { echo "RDC device state is invalid; refusing to persist it."; exit 2; }

openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
  -in "$RAW_DEVICE" -out "$ENC" -pass env:RDC_STATE_KEY

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
