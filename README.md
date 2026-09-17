# GitHub Runner Lab

A disposable GitHub Actions lab for testing Remote Desktop Commander on an ephemeral Ubuntu runner without touching the production Tailscale Exit Node repository.

## Goals

- Start only by manual `workflow_dispatch`.
- Install a pinned Remote Desktop Commander version.
- Allow one-time manual pairing when no saved device identity is configured.
- Support automatic reconnect when `RDC_DEVICE_STATE_B64` is added as a repository secret.
- Keep the runner alive for a selectable test window and restart the RDC process if it crashes.
- Never modify the production Exit Node workflow.

## First test

Open **Actions → Remote Desktop Commander Lab → Run workflow**. If `RDC_DEVICE_STATE_B64` is not configured, the workflow log will show the normal RDC pairing flow. Complete the pairing in your browser, then use ChatGPT to verify that the runner appears online.

## Automatic reconnect

For a persistent RDC identity across ephemeral runners, store the paired `~/.desktop-commander-device/device.json` as a Base64-encoded GitHub Actions secret named `RDC_DEVICE_STATE_B64`.

Do not commit `device.json` or its Base64 value to this repository. Treat it as a credential. Prefer a dedicated RDC identity for this lab; do not run two machines simultaneously with the same restored identity.

## Safety

This repository intentionally has no schedule, watchdog, self-relaunch chain, exit-node advertisement, or production secrets. It is a disposable test bed only.
