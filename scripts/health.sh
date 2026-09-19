#!/usr/bin/env bash
set -Eeuo pipefail
python3 - <<'PY'
import json,os,time
from pathlib import Path
try:
    pid=int(Path(os.getenv('RDC_PID_FILE','/tmp/rdc.pid')).read_text())
    os.kill(pid,0)
    state=json.loads(Path(os.getenv('RDC_HEALTH_FILE','/tmp/rdc-health.json')).read_text())
    healthy=state['pid']==pid and state['healthy'] and 0 <= time.time()*1000-state['sampled_at'] < 20000
    print('RDC_HEALTH=' + ('READY' if healthy else 'DEGRADED'))
    raise SystemExit(0 if healthy else 1)
except (OSError,ValueError,KeyError):
    print('RDC_HEALTH=UNAVAILABLE')
    raise SystemExit(1)
PY
