#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required}"
if (( ${#RDC_STATE_KEY} < 32 )); then
  echo "RDC_STATE_KEY must be at least 32 characters long."
  exit 2
fi

STATE_BRANCH="${RDC_STATE_BRANCH:-rdc-state}"
STATE_PATH=".rdc-state/device.json.enc"
STATE_OWNER="${RDC_STATE_OWNER:-rdc-agent}"
DEST_FILE="${RDC_STATE_FILE:-$(getent passwd "$STATE_OWNER" | cut -d: -f6)/.desktop-commander-device/device.json}"
TMP_DIR="$(mktemp -d)"
ENC_FILE="$TMP_DIR/device.json.enc"
TMP_FILE="$TMP_DIR/device.json"
chmod 0700 "$TMP_DIR"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

if ! git fetch --quiet --depth=1 origin "refs/heads/${STATE_BRANCH}" 2>/dev/null; then
  echo "No persistent RDC state branch exists yet."
  exit 3
fi
if ! git show "FETCH_HEAD:${STATE_PATH}" > "$ENC_FILE" 2>/dev/null; then
  echo "Persistent RDC state file is missing."
  exit 3
fi

if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -in "$ENC_FILE" -out "$TMP_FILE" -pass env:RDC_STATE_KEY 2>/dev/null; then
  echo "Encrypted RDC state could not be decrypted. Check RDC_STATE_KEY."
  exit 2
fi
node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.access_token||!j.session?.refresh_token)process.exit(2)" "$TMP_FILE" \
  || { echo "Decrypted RDC state is invalid."; exit 2; }

sudo install -d -o "$STATE_OWNER" -g "$STATE_OWNER" -m 0700 "$(dirname "$DEST_FILE")"
sudo install -o "$STATE_OWNER" -g "$STATE_OWNER" -m 0600 "$TMP_FILE" "$DEST_FILE"
echo "Encrypted RDC state restored into the isolated Agent account."
