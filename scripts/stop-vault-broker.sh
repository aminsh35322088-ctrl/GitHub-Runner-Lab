#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo 'stop-vault-broker.sh must run as root.' >&2
  exit 2
fi
PID_FILE="${VAULT_BROKER_PID_FILE:-/run/runner-vault/broker.pid}"
SOCKET="${VAULT_BROKER_SOCKET:-/run/runner-vault/broker.sock}"
BROKER_USER="${VAULT_BROKER_USER:-runner-vault-broker}"
BROKER_HOME="${VAULT_BROKER_HOME:-/var/lib/runner-vault-broker}"

# Match the exact service identity, entrypoint and socket instead of trusting
# a possibly stale/reused PID file. This also finds the process if PID setup
# was interrupted after the Broker began listening.
set +e
/usr/bin/python3 - "$BROKER_USER" "$BROKER_HOME/vault_broker.py" "$SOCKET" <<'PY_STOP_BROKER'
import os
from pathlib import Path
import pwd
import signal
import sys
import time

user, entrypoint, socket_path = sys.argv[1:]
try:
    expected_uid = pwd.getpwnam(user).pw_uid
except KeyError:
    raise SystemExit(f"Broker service identity {user!r} does not exist")

def matching_pids():
    matches = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            status = (proc / 'status').read_text()
            uid_line = next(line for line in status.splitlines() if line.startswith('Uid:'))
            real_uid = int(uid_line.split()[1])
            argv = [part.decode('utf-8', 'replace') for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
        except (OSError, StopIteration, ValueError):
            continue
        if real_uid != expected_uid or len(argv) < 4:
            continue
        if argv[1] == entrypoint and argv[2] == 'serve' and len(argv) >= 6:
            try:
                socket_index = argv.index('--socket')
            except ValueError:
                continue
            if socket_index + 1 < len(argv) and argv[socket_index + 1] == socket_path:
                matches.append(int(proc.name))
    return matches

pids = matching_pids()
for pid in pids:
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass

deadline = time.monotonic() + 2.0
while time.monotonic() < deadline:
    pids = matching_pids()
    if not pids: break
    time.sleep(0.05)

for pid in matching_pids():
    try: os.kill(pid, signal.SIGKILL)
    except ProcessLookupError: pass

deadline = time.monotonic() + 1.0
remaining = matching_pids()
while remaining and time.monotonic() < deadline:
    time.sleep(0.05)
    remaining = matching_pids()
if remaining:
    print('Vault Broker process termination could not be confirmed.', file=sys.stderr)
    raise SystemExit(5)
PY_STOP_BROKER
STOP_STATUS=$?
set -e

# Always revoke the filesystem endpoint, even if process termination could not
# be confirmed. A surviving process then has no pathname the Agent can connect to.
rm -f -- "$PID_FILE" "$SOCKET"
if [[ -e "$SOCKET" || -L "$SOCKET" || -e "$PID_FILE" ]]; then
  echo 'Vault Broker socket/PID cleanup could not be confirmed.' >&2
  exit 6
fi
if [[ "$STOP_STATUS" -ne 0 ]]; then
  echo 'VAULT_BROKER=SOCKET_REVOKED_PROCESS_STOP_UNCONFIRMED' >&2
  exit "$STOP_STATUS"
fi
echo 'VAULT_BROKER=STOPPED'
