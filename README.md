# GitHub Runner Lab

A self-relaunching GitHub Actions lab for keeping a disposable Ubuntu runner reachable through Remote Desktop Commander without modifying the stable Tailscale Exit Node repository.

## Architecture

- `rdc-lab.yml` runs the active lab for about 330 minutes, then pre-queues a successor before releasing its concurrency lock.
- `rdc-watchdog.yml` reacts to completed runs and also checks every 10 minutes as a backstop.
- `.github/scripts/ensure-rdc-lab.sh` prevents duplicate active runners, replaces stale/pending runs, and includes a short-failure crash-loop guard.
- `repository-heartbeat.yml` periodically keeps this public repository active so scheduled workflows remain eligible.
- RDC 0.2.50 is installed fresh on every runner and its process is locally restarted if it crashes.

## RDC identity handoff

RDC requires one initial browser/device authorization. The resulting `device.json` is the only manual bootstrap required.

Create the repository Actions secret `RDC_DEVICE_STATE_B64` from a dedicated, paired RDC identity. Never commit `device.json` or its Base64 value.

After bootstrap, each runner restores an encrypted rolling state from GitHub Actions cache, starts RDC with the same identity, checkpoints the current session every five minutes, gracefully saves it before handoff, and encrypts the state before caching it for the successor.

The cache copy is encrypted with AES-256-CBC/PBKDF2. Its encryption passphrase is derived at runtime from the repository secret and is never committed.

## RDC 0.2.50 restart hardening

RDC 0.2.50 can rotate its refresh token in memory without rewriting the persisted `device.json`. That is unsafe for ephemeral runners because a later process may restore a stale refresh token.

`scripts/patch-rdc-refresh.sh` applies a narrow runtime patch to the freshly installed package so the current session is periodically persisted and saved again during graceful shutdown. The upstream package in npm and this repository's source remain untouched.

## Automatic lifecycle

Once `RDC_DEVICE_STATE_B64` exists, no normal manual restart is required. The active run keeps RDC online, pre-queues its successor, transfers encrypted session state, and the watchdog repairs a broken relaunch chain.

There can still be a short GitHub-hosted runner startup/handover gap; this design is self-healing and near-continuous rather than a literal single machine with uninterrupted uptime.

If the bootstrap secret is absent, both the lab and watchdog intentionally remain dormant instead of entering a failing relaunch loop.

## Isolation

This repository does not modify `GitHub-Tailscale-Exit-Node`, its Tailscale OAuth credentials, exit-node advertisements, SSH configuration, or watchdog chain. The two automation systems are independent.
