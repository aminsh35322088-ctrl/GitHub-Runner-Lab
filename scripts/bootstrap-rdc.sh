#!/usr/bin/env bash
set -Eeuo pipefail

RDC_DIR="$HOME/.desktop-commander-device"
DEVICE_FILE="$RDC_DIR/device.json"
LOG_FILE="${RDC_BOOTSTRAP_LOG:-/tmp/rdc-bootstrap.log}"
TIMEOUT_SECONDS="${RDC_BOOTSTRAP_TIMEOUT_SECONDS:-600}"

mkdir -p "$RDC_DIR"
chmod 700 "$RDC_DIR"
rm -f "$DEVICE_FILE"
: > "$LOG_FILE"

valid_state() {
  node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));if(!j.deviceId||!j.session?.access_token||!j.session?.refresh_token)process.exit(2)" "$1" >/dev/null 2>&1
}

echo "Starting one-time RDC account authorization."
echo "Open the verification URL/code printed below and authorize it with YOUR Desktop Commander account."
echo "This step waits up to $((TIMEOUT_SECONDS / 60)) minutes."

# Keep the official first-run flow visible in the Actions log, but never print
# device.json itself. The resulting credential is persisted separately in an
# encrypted state branch.
npx -y @wonderwhy-er/desktop-commander@latest remote \
  > >(tee -a "$LOG_FILE") 2>&1 &
RDC_PID=$!

cleanup() {
  if kill -0 "$RDC_PID" 2>/dev/null; then
    kill -TERM "$RDC_PID" 2>/dev/null || true
    wait "$RDC_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for ((elapsed=0; elapsed<TIMEOUT_SECONDS; elapsed+=2)); do
  if [[ -s "$DEVICE_FILE" ]] && valid_state "$DEVICE_FILE" && grep -q 'Device ready' "$LOG_FILE" 2>/dev/null; then
    chmod 600 "$DEVICE_FILE"
    echo "RDC authorization completed and device state was created."
    cleanup
    trap - EXIT INT TERM
    exit 0
  fi

  if ! kill -0 "$RDC_PID" 2>/dev/null; then
    echo "RDC exited before authorization completed."
    exit 1
  fi
  sleep 2
done

echo "Timed out waiting for RDC account authorization. Re-run the workflow with bootstrap=true."
exit 1
