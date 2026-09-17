# GitHub Runner Lab

A disposable GitHub Actions lab for testing Remote Desktop Commander on an ephemeral Ubuntu runner without touching the production Tailscale Exit Node repository.

## What it does

- Starts only by manual `workflow_dispatch`.
- Uses Node.js 22.14.0 and Remote Desktop Commander 0.2.50.
- Restores an RDC device identity from the GitHub Actions secret `RDC_DEVICE_STATE_B64`.
- Keeps the runner online for 15, 30, 60, 120, or 300 minutes.
- Restarts the RDC process if it crashes, up to three times.
- Includes `scripts/health.sh` for a compact runner/RDC diagnostic snapshot.

## Required one-time secret

This repository is public, so manual RDC pairing inside Actions is intentionally disabled: a pairing code must never be exposed in public workflow logs.

Create a repository Actions secret named `RDC_DEVICE_STATE_B64` containing the Base64 form of a paired `~/.desktop-commander-device/device.json` file. Do not commit the file or its Base64 value.

If you reuse an identity that is currently running on another machine, stop that RDC agent before starting this lab. A dedicated lab identity is preferable.

## Run the lab

Open **Actions → Remote Desktop Commander Lab → Run workflow**, select a duration, and start it. Once RDC reconnects, the ephemeral GitHub runner should appear as the paired device in ChatGPT/Remote Desktop Commander.

## Safety boundary

This lab has no schedule, watchdog, self-relaunch chain, Tailscale exit-node advertisement, or production Exit Node secrets. The production `GitHub-Tailscale-Exit-Node` repository is not modified by this lab.
