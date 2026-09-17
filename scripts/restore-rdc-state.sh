#!/usr/bin/env bash
set -euo pipefail

: "${RDC_DEVICE_STATE_B64:?RDC_DEVICE_STATE_B64 secret is required}"

STATE_DIR="${RDC_STATE_DIR:-${GITHUB_WORKSPACE:-$PWD}/.rdc-state}"
ENC_FILE="$STATE_DIR/device.json.enc"
DEST_DIR="$HOME/.desktop-commander-device"
DEST_FILE="$DEST_DIR/device.json"

mkdir -p "$STATE_DIR" "$DEST_DIR"
chmod 700 "$DEST_DIR"
umask 077

PASSPHRASE="$(printf '%s' "$RDC_DEVICE_STATE_B64" | sha256sum | awk '{print $1}')"
export RDC_STATE_PASSPHRASE="$PASSPHRASE"

if [[ -s "$ENC_FILE" ]]; then
  echo "Restoring RDC identity from encrypted rolling state."
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -in "$ENC_FILE" -out "$DEST_FILE.tmp" -pass env:RDC_STATE_PASSPHRASE
else
  echo "No rolling state found; using one-time bootstrap identity from GitHub Secret."
  printf '%s' "$RDC_DEVICE_STATE_B64" | base64 --decode > "$DEST_FILE.tmp"
fi

node -e "const fs=require('fs');const p=process.argv[1];const j=JSON.parse(fs.readFileSync(p,'utf8'));if(!j.deviceId||!j.session?.refresh_token)process.exit(2)" "$DEST_FILE.tmp"
mv "$DEST_FILE.tmp" "$DEST_FILE"
chmod 600 "$DEST_FILE"
unset RDC_STATE_PASSPHRASE PASSPHRASE

echo "RDC state restored and validated."
