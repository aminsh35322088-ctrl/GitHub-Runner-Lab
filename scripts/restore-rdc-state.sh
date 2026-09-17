#!/usr/bin/env bash
set -euo pipefail

: "${RDC_DEVICE_STATE_B64:?RDC_DEVICE_STATE_B64 secret is required}"

STATE_DIR="${RDC_STATE_DIR:-${GITHUB_WORKSPACE:-$PWD}/.rdc-state}"
ENC_FILE="$STATE_DIR/device.json.enc"
DEST_DIR="$HOME/.desktop-commander-device"
DEST_FILE="$DEST_DIR/device.json"
TMP="$DEST_FILE.tmp"

mkdir -p "$STATE_DIR" "$DEST_DIR"
chmod 700 "$DEST_DIR"
umask 077

PASSPHRASE="$(printf '%s' "$RDC_DEVICE_STATE_B64" | sha256sum | awk '{print $1}')"
export RDC_STATE_PASSPHRASE="$PASSPHRASE"

valid_state() {
  node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.refresh_token)process.exit(2)" "$1" >/dev/null 2>&1
}

RESTORED=false
if [[ -s "$ENC_FILE" ]]; then
  echo "Trying encrypted rolling RDC state."
  if openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
      -in "$ENC_FILE" -out "$TMP" -pass env:RDC_STATE_PASSPHRASE 2>/dev/null \
      && valid_state "$TMP"; then
    RESTORED=true
    echo "Encrypted rolling state restored."
  else
    echo "Rolling state is unavailable or invalid; falling back to bootstrap identity."
    rm -f "$TMP"
  fi
fi

if [[ "$RESTORED" != "true" ]]; then
  echo "Restoring one-time bootstrap identity from GitHub Secret."
  # Canonicalize only the first valid JSON object. This tolerates accidental
  # trailing shell/prompt bytes in an older Base64 bootstrap value without ever
  # printing the credential to Actions logs.
  python3 - "$TMP" <<'PY'
import base64, json, os, sys

raw = ''.join(os.environ['RDC_DEVICE_STATE_B64'].split())
try:
    decoded = base64.b64decode(raw + '=' * ((4 - len(raw) % 4) % 4), validate=False)
except Exception as exc:
    raise SystemExit(f'Bootstrap Base64 could not be decoded: {exc}')

text = decoded.decode('utf-8', errors='ignore').lstrip('\ufeff\x00\r\n\t ')
try:
    obj, _ = json.JSONDecoder().raw_decode(text)
except Exception as exc:
    raise SystemExit(f'Bootstrap does not begin with valid RDC JSON: {exc}')

if not isinstance(obj, dict) or not obj.get('deviceId'):
    raise SystemExit('Bootstrap JSON has no deviceId')
session = obj.get('session') or {}
if not session.get('refresh_token') or not session.get('access_token'):
    raise SystemExit('Bootstrap JSON has no persisted RDC session')

with open(sys.argv[1], 'w', encoding='utf-8') as f:
    json.dump(obj, f, indent=2)
    f.write('\n')
PY
fi

valid_state "$TMP" || { echo "RDC state validation failed."; exit 1; }
mv "$TMP" "$DEST_FILE"
chmod 600 "$DEST_FILE"
unset RDC_STATE_PASSPHRASE PASSPHRASE

echo "RDC state restored and validated."
