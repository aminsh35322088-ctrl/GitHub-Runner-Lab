# GitHub Runner Lab

A self-relaunching GitHub Actions lab that keeps an ephemeral Ubuntu runner reachable through Remote Desktop Commander (RDC) and prewarms it for heavy development work.

<!-- RDC-LAB-STATUS:START -->

## ⏱️ Live Runner status

| Item | Value |
| :--- | :--- |
| Runner state | 🟢 RDC verified · keepalive running |
| Agent work mode | 🟡 CAUTION — finish bounded work only |
| Last checked | 2026-09-18 16:37:33 UTC |
| Runner/job started | 2026-09-18 11:54:06 UTC |
| Keepalive started | 2026-09-18 11:54:37 UTC |
| Runner age | 283 min |
| Safe remaining | 46 min |
| Nominal handoff | 2026-09-18 17:24:06 UTC |
| Hard job timeout | 2026-09-18 17:44:06 UTC |
| Planned timeout headroom | 20 min |
| Successor already queued | ✅ yes |
| RDC verification success | 50% (8 samples) |
| Keepalive completion | 40% (5 samples) |
| Handoff ≤15 min | 83% (12 samples) |
| Median handoff gap | 0.0 min |
| Run details | [Open current run](https://github.com/aminsh35322088-ctrl/GitHub-Runner-Lab/actions/runs/35340501983) |

> Refreshed on workflow events and about every 10 minutes. For the exact local clock while connected, run `./scripts/agent-run.sh status`. The lifecycle clock starts during early runner setup: handoff is planned at 330 minutes with a 350-minute hard job timeout. Reliability percentages are measured from the corresponding workflow steps in up to the last 12 completed runs; handoff reliability means the next run started within 15 minutes.

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

The workflow installs the current Desktop Commander package on each fresh runner. The repository does not carry a version-specific runtime patch.

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
- `scripts/agent-checkpoint.sh` — snapshots tracked changes and unpushed commits without copying untracked file contents.
- `scripts/package-agent-checkpoints.sh` — encrypts the latest snapshot before artifact upload.
- `scripts/agent-workspace.sh` — safe branch/PR checkout under `~/agent-workspaces`.
- `scripts/agent-project.sh` — invokes the target branch's project-specific Agent Lab runner using an external persistent cache.
- `scripts/agent-handoff.sh` — requests the guarded clean restart path in the final 20-minute window.
- `scripts/agent-doctor.sh` — compact machine/repository context report.
- `scripts/agent-run.sh` — one-call entry point for status, prepare, workspace and bootstrap.
- RDC lifecycle scripts — bootstrap, restore, start, health, keepalive, stop and persist.

Canonical prewarm files:

```text
~/.cache/agent-runner-kit/prewarm.env
~/.cache/agent-runner-kit/prewarm.log
```

A failed prewarm does not take RDC offline. READY state is versioned and revalidated against the required command set, so toolchain changes cannot leave a stale green marker. The lifecycle clock starts during early workflow setup. It stays SAFE until 20 minutes remain, then switches to the restart window and rotates to a fresh runner instead of making the agent abandon work 30–60 minutes early.

## Test policy

This Lab can run heavy local builds and tests when the target repository permits it. For `opencode-telegram-bot`, its own `AGENTS.md` requires the full suite to run in GitHub Actions CI because Railway production is resource-constrained; that repository policy takes precedence.

## Isolation

This repository is independent from `GitHub-Tailscale-Exit-Node`. Do not modify that stable exit-node system from this Lab unless explicitly requested.
