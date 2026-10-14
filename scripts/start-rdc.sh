#!/usr/bin/env bash
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RDC_DIR="$HOME/.desktop-commander-device"
DEVICE_FILE="$RDC_DIR/device.json"
LOG_FILE="${RDC_LOG_FILE:-/tmp/rdc.log}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"
STARTUP_SECONDS="${RDC_STARTUP_TIMEOUT_SECONDS:-180}"
[[ "$STARTUP_SECONDS" =~ ^[1-9][0-9]*$ ]] && (( STARTUP_SECONDS <= 300 )) || exit 2

startup_diagnostics() {
  # Only fixed phase names/booleans: raw RDC logs include session and account data.
  python3 - "$LOG_FILE" <<'PYDIAG'
import json,os,sys
from pathlib import Path
text=Path(sys.argv[1]).read_text(errors='replace') if Path(sys.argv[1]).exists() else ''
for label,needle in [('local_connected','Connected to Desktop Commander MCP'),
                     ('session_restored','Session restored'),
                     ('reauthorization_required','Please complete authentication'),
                     ('session_invalid','Persisted session invalid'),
                     ('channel_unreachable','Realtime channel is not open'),
                     ('device_ready','Device ready'),('startup_failed','Device startup failed')]:
    print(f'RDC_STARTUP_{label.upper()}={str(needle in text).lower()}')
try:
    state=json.loads(Path(os.getenv('RDC_HEALTH_FILE','/tmp/rdc-health.json')).read_text())
    for key in ('healthy','reachable','local_executor_ready','channel_joined','presence_tracked'):
        print(f'RDC_{key.upper()}={state.get(key) is True}')
except (OSError,ValueError):
    print('RDC_SAMPLE_AVAILABLE=false')
PYDIAG
}
trap 'code=$?; if (( code != 0 )); then startup_diagnostics; fi' EXIT

[[ -s "$DEVICE_FILE" ]] || { echo "RDC device state was not restored."; exit 2; }
mkdir -p "$RDC_DIR"
chmod 700 "$RDC_DIR"
chmod 600 "$DEVICE_FILE"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "RDC is already running."
  exit 0
fi

: > "$LOG_FILE"
nohup env   NODE_OPTIONS=--dns-result-order=ipv4first   RUNNER_TRACKING_ID=rdc-runner-lab   node "$SELF_DIR/rdc-supervisor.mjs"   >"$LOG_FILE" 2>&1 < /dev/null &

PID=$!
echo "$PID" > "$PID_FILE"
echo "RDC agent started; waiting for authenticated readiness."

for _ in $(seq 1 "$STARTUP_SECONDS"); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "RDC exited during startup. Check the private state/bootstrap configuration."
    exit 1
  fi
  if "$SELF_DIR/health.sh" >/dev/null 2>&1; then
    echo "RDC authenticated and ready."
    exit 0
  fi
  if grep -q 'Please complete authentication' "$LOG_FILE" 2>/dev/null; then
    echo "RDC requires interactive re-authorization; stored identity is no longer valid."
    exit 1
  fi
  sleep 1
done

echo "RDC did not reach ready state within $STARTUP_SECONDS seconds."
exit 1
