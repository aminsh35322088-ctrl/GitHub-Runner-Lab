#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="${RDC_LOG_FILE:-/tmp/rdc.log}"
PID_FILE="${RDC_PID_FILE:-/tmp/rdc.pid}"

echo "=== Runner ==="
echo "host=$(hostname)"
echo "kernel=$(uname -sr)"
echo "uptime=$(uptime -p)"

echo "=== Resources ==="
free -h || true
df -h / || true

echo "=== Remote Desktop Commander ==="
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  PID="$(cat "$PID_FILE")"
  echo "status=running pid=$PID"
  ps -p "$PID" -o pid,ppid,etime,%cpu,%mem,rss --no-headers || true
  if grep -q 'Device ready' "$LOG_FILE" 2>/dev/null; then
    echo "remote_status=ready"
  else
    echo "remote_status=starting-or-degraded"
  fi
else
  echo "status=not-running"
fi

echo "=== Git ==="
git status --short --branch 2>/dev/null || true
