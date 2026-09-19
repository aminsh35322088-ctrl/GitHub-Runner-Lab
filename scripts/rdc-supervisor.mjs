// Version-specific adapter for pinned RDC 0.2.51. No package files are modified.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
const { MCPDevice } = await import(pathToFileURL(join(root, '@wonderwhy-er/desktop-commander/dist/remote-device/device.js')));
const file = process.env.RDC_HEALTH_FILE || '/tmp/rdc-health.json';
mkdirSync(dirname(file), { recursive: true });
const device = new MCPDevice({ persistSession: true });
let lastReady = null;
function sample() {
  const channel = device.remoteChannel;
  const last = channel?.lastHeartbeatOkAt;
  const reachable = channel?.isReachable?.() === true;
  const fresh = typeof last === 'number' && performance.now() - last < 90000;
  const healthy = reachable && fresh && !device.isShuttingDown;
  if (healthy) lastReady = Date.now();
  const state = { pid: process.pid, sampled_at: Date.now(), healthy, reachable,
    heartbeat_age_ms: typeof last === 'number' ? performance.now() - last : null,
    last_ready_at: lastReady };
  writeFileSync(file + '.tmp', JSON.stringify(state), { mode: 0o600 });
  renameSync(file + '.tmp', file);
}
sample();
setInterval(sample, 5000).unref();
await device.start();
sample();
