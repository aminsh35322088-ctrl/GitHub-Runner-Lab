#!/usr/bin/env bash
set -euo pipefail

ROOT="$(npm root --global)"
TARGET="$ROOT/@wonderwhy-er/desktop-commander/dist/remote-device/device.js"

[[ -f "$TARGET" ]] || { echo "RDC device module not found: $TARGET"; exit 1; }

python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text()
marker = 'RDC_RUNNER_LAB_SESSION_PERSIST'
if marker in s:
    print('RDC refresh persistence hotfix already present.')
    raise SystemExit(0)

heartbeat = 'this.remoteChannel.startHeartbeat(this.deviceId);'
if heartbeat not in s:
    raise SystemExit('Could not locate RDC heartbeat hook; refusing an unsafe patch.')

periodic = heartbeat + r'''
            // RDC_RUNNER_LAB_SESSION_PERSIST: v0.2.50 rotates refresh tokens in
            // memory but does not persist the rotated session. Keep device.json
            // current so a disposable GitHub runner can hand the identity over.
            const rdcRunnerLabPersist = setInterval(async () => {
                try {
                    const current = await this.remoteChannel.getSession();
                    if (current?.data?.session?.refresh_token) {
                        await this.savePersistedConfig();
                    }
                } catch (error) {
                    console.warn('[RDC Lab] session checkpoint failed:', error?.message || error);
                }
            }, 120000);
            rdcRunnerLabPersist.unref?.();'''
s = s.replace(heartbeat, periodic, 1)

shutdown = 'this.isShuttingDown = true;'
if shutdown not in s:
    raise SystemExit('Could not locate RDC shutdown hook; refusing an unsafe patch.')
shutdown_patch = shutdown + r'''
        // RDC_RUNNER_LAB_SESSION_PERSIST: persist the newest rotated token
        // immediately before the channel is torn down.
        try {
            await this.savePersistedConfig();
        } catch (error) {
            console.warn('[RDC Lab] final session checkpoint failed:', error?.message || error);
        }'''
s = s.replace(shutdown, shutdown_patch, 1)

p.write_text(s)
print('Applied RDC v0.2.50 refresh-token persistence hotfix.')
PY

node --check "$TARGET"
