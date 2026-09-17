#!/usr/bin/env bash
set -euo pipefail

: "${RDC_DEVICE_STATE_B64:?RDC_DEVICE_STATE_B64 secret is required}"

SRC="$HOME/.desktop-commander-device/device.json"
STATE_DIR="${RDC_STATE_DIR:-${GITHUB_WORKSPACE:-$PWD}/.rdc-state}"
ENC_FILE="$STATE_DIR/device.json.enc"

[[ -s "$SRC" ]] || { echo "RDC state file is missing."; exit 1; }
node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.refresh_token)process.exit(2)" "$SRC"

mkdir -p "$STATE_DIR"
umask 077
PASSPHRASE="$(printf '%s' "$RDC_DEVICE_STATE_B64" | sha256sum | awk '{print $1}')"
export RDC_STATE_PASSPHRASE="$PASSPHRASE"

openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -in "$SRC" -out "$ENC_FILE.tmp" -pass env:RDC_STATE_PASSPHRASE
mv "$ENC_FILE.tmp" "$ENC_FILE"
chmod 600 "$ENC_FILE"
unset RDC_STATE_PASSPHRASE PASSPHRASE

echo "Encrypted RDC rolling state checkpoint updated."
