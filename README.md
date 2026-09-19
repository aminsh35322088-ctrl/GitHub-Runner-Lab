# GitHub Runner Lab

A self-relaunching GitHub Actions lab that keeps an ephemeral Ubuntu runner reachable through Remote Desktop Commander (RDC) and prewarms it for heavy development work.

<!-- RDC-LAB-STATUS:START -->

## ⏱️ Live Runner status

| Item | Value |
| :--- | :--- |
| Runner state | 🟢 RDC verified · keepalive running |
| Agent work mode | 🟢 SAFE — normal work window |
| Last checked | 2026-09-19 11:34:09 UTC |
| Runner/job started | 2026-09-19 11:20:33 UTC |
| Keepalive started | 2026-09-19 11:21:08 UTC |
| Runner age | 13 min |
| Runner lifecycle remaining | 316 min |
| Auto restart threshold | 20 min remaining |
| Nominal handoff | 2026-09-19 16:50:33 UTC |
| Hard job timeout | 2026-09-19 17:10:33 UTC |
| Planned timeout headroom | 20 min |
| Successor already queued | ✅ yes |
| RDC verification success | 100% (7 samples) |
| Keepalive completion | 57% (7 samples) |
| Handoff ≤15 min | 100% (12 samples) |
| Median handoff gap | 0.0 min |
| Run details | [Open current run](https://github.com/aminsh35322088-ctrl/GitHub-Runner-Lab/actions/runs/35439822385) |

> Refreshed on workflow events and about every 10 minutes. For the exact local clock while connected, run `./scripts/agent-run.sh status`. The lifecycle clock starts during early runner setup: handoff is planned at 330 minutes with a 350-minute hard job timeout. Normal work remains SAFE until the final 20 minutes, when the current run checkpoints and rotates to a fresh runner. Reliability percentages are measured from the corresponding workflow steps in up to the last 12 completed runs; handoff reliability means the next run started within 15 minutes.

<!-- RDC-LAB-STATUS:END -->

## Lifecycle

The lifecycle clock targets handoff 330 minutes after runner initialization, while the job hard timeout is 350 minutes. Normal agent work remains SAFE until the final 20 minutes. At that point the keepalive path checkpoints work, confirms/queues a successor, ends cleanly, and lets workflow finalizers persist RDC state before the fresh runner starts. `rdc-watchdog.yml` is the recovery backstop, and `repository-heartbeat.yml` keeps scheduled workflows eligible.

Startup is intentionally ordered for fast remote access:

1. Checkout and restore encrypted RDC state from the private `rdc-state` branch.
2. Install/start RDC and verify authenticated health.
3. Enter the long-lived keepalive step and launch the full agent toolchain prewarm in its background.
4. Keep RDC online while prewarm finishes.
5. Persist rotated RDC state and hand off to the successor runner.

Heavy package installation therefore never blocks initial RDC connectivity.

## RDC state

The only required persistence secret is `RDC_STATE_KEY` (at least 32 random characters). The encrypted `device.json` is stored on the dedicated `rdc-state` branch; plaintext credentials are never committed.

For first setup or recovery, manually dispatch `rdc-lab.yml` with `bootstrap=true` and complete the RDC browser authorization. Normal successor runs restore state unattended.

The workflow installs Desktop Commander `0.2.51` on each fresh runner. `rdc-supervisor.mjs` wraps that pinned runtime and publishes a fresh health sample only while its remote channel remains reachable.

`RDC_STATE_KEY` also authenticates and encrypts Agent checkpoints. For broader, command-scoped GitHub access, an optional fine-grained PAT can be stored as the `AGENT_GITHUB_TOKEN` repository secret. Grant only the repositories and permissions the Agent actually needs. The token is installed after RDC becomes healthy, is never copied into managed job environments, caches, or checkpoints, and is removed during finalization. Normal checkout and lifecycle writes continue to use `GITHUB_TOKEN`.

A practical fine-grained PAT baseline is **Contents: read/write** for the selected development repositories and **Pull requests: read/write** when the Agent should create or update PRs. Add **Actions: read/write** only when it must dispatch or rerun workflows, and add Issues or other permissions only for tasks that use them. Give the token an expiry and rotate the repository secret before it expires.

## Agent environment

Read `AGENTS.md` before using the Lab. The target repository's own agent instructions remain authoritative.

The one-call status check is:

```bash
./scripts/agent-run.sh status
```

A typical project-aware workspace preparation is:

```bash
./scripts/agent-run.sh work --repo https://github.com/OWNER/REPO.git --ref main
```

Project-specific setup/test policy lives in the target branch at `.github/agent-lab/runner.sh`, not in this Lab. The workflow persists `~/.cache/agent-projects` and `~/.npm` between runner generations so branch-owned setup scripts can reuse dependency environments and package downloads.

Use managed jobs for bounded builds and tests:

```bash
job="$(./scripts/agent-run.sh job start --cwd "$PWD" --timeout 1800 -- npm test)"
./scripts/agent-run.sh job wait "$job"
```

Each job gets a clean environment, durable JSON result, capped combined output, timeout/cancellation, approximate process-group peak RSS, and one active job per workspace. Environment variables cross the boundary only through repeated `--pass-env NAME`. Container jobs add `--image IMAGE` and default to no network, dropped capabilities, bounded memory/CPU/PIDs, and no Docker socket. Use `--network bridge` only when a test requires outbound access.

For this project's default OpenCode Telegram bot repository:

```bash
./scripts/agent-run.sh prepare --pr 99
```

The automatic full prewarm covers the common coding/debugging stack: Git/GitHub CLI, Node/npm, Python, ripgrep/fd/fzf, jq/yq, C/C++ build tools, CMake, Ninja, Clang, GDB, Git LFS, SQLite, diagnostics/network tools, FFmpeg, and ImageMagick. GitHub-hosted tools such as Docker remain available when provided by the runner image.

Large specialized SDKs such as Android, Rust, uncommon JDKs, Playwright browser bundles, or database servers remain on-demand.

## Helper scripts

- `scripts/agent-lib.sh` — shared toolchain version/readiness contract.
- `scripts/agent-bootstrap.sh` — idempotent core/build/media/full prerequisite installer.
- `scripts/agent-prewarm.sh` — self-contained foreground/background full prewarm with locking and canonical state/log files.
- `scripts/agent-status.sh` — compact toolchain + exact local runner countdown report.
- `scripts/agent-runtime.sh` — initializes and reports the local 330-minute handoff timer.
- `scripts/agent-checkpoint.sh` — creates verified Git bundles, staged/unstaged patches, and filtered untracked source snapshots for clones and linked worktrees.
- `scripts/package-agent-checkpoints.sh` — authenticates and encrypts the latest snapshot before persistence.
- `scripts/checkpoint-sync.sh` — restores or saves the encrypted snapshot through the dedicated `agent-checkpoints` branch.
- `scripts/agent-workspace.sh` — prepares branch/PR workspaces under `~/agent-workspaces` and refuses dirty, divergent, or unpublished state instead of resetting it.
- `scripts/agent-project.sh` — invokes the target branch's project-specific Agent Lab runner using an external persistent cache.
- `scripts/lab_jobs.py` — starts, observes, stops, drains, and reports bounded detached jobs.
- `scripts/lab_ready.py` — emits a read-only JSON readiness report.
- `scripts/agent-github.sh` — exposes the optional PAT only to an explicit `gh`, `git`, or authenticated workspace operation.
- `scripts/lab_fault_server.py` — provides loopback-only delay, error, streaming, and disconnect fixtures.
- `scripts/agent-handoff.sh` — requests the guarded clean restart path in the final 20-minute window.
- `scripts/agent-doctor.sh` — compact machine/repository context report.
- `scripts/agent-run.sh` — one-call entry point for workspace, jobs, checkpoints, GitHub access, readiness, cache cleanup, and toolchain operations.
- RDC lifecycle scripts — bootstrap, restore, start, health, keepalive, stop and persist.

Canonical prewarm files:

```text
~/.cache/agent-runner-kit/prewarm.env
~/.cache/agent-runner-kit/prewarm.log
```

A failed prewarm does not take RDC offline. READY state is versioned and revalidated against the required command set, so toolchain changes cannot leave a stale green marker. The lifecycle clock starts during early workflow setup. It stays SAFE until 20 minutes remain, then switches to the restart window and rotates to a fresh runner instead of making the agent abandon work 30–60 minutes early.

## Test policy

This Lab can run heavy local builds and tests when the target repository permits it. In this project, validation is launched through Remote Desktop Commander so the test process runs inside the same engineered runner environment users receive. A target repository's own policy still takes precedence; for example, `opencode-telegram-bot` reserves its full suite for GitHub Actions CI and permits only targeted local validation unless that policy changes.

## Isolation

This repository is independent from `GitHub-Tailscale-Exit-Node`. Do not modify that stable exit-node system from this Lab unless explicitly requested.
