# AGENTS.md — GitHub Runner Lab

## Purpose

This repository runs an ephemeral GitHub-hosted Ubuntu machine that stays reachable through Remote Desktop Commander (RDC). Treat it as a disposable **heavy-work execution environment** for coding agents: source inspection, large builds, dependency-heavy debugging, media tooling, targeted tests, compilers, and other work that should not run on constrained production services.

The machine is temporary. Durable code belongs in GitHub, not only on the runner filesystem.

## Precedence

1. Read this file before using the Lab.
2. Before changing or testing another repository, read that target repository's own `AGENTS.md`, `CLAUDE.md`, or equivalent instructions.
3. The target repository's explicit policy overrides this Lab's defaults.
4. Example: `opencode-telegram-bot` intentionally runs its **full test suite in GitHub Actions CI** because Railway production is resource-constrained. Do not move that full-suite validation onto Railway or this Lab unless its own instructions are changed. The Lab can still be used for source inspection, targeted debugging, reproduction, compilation, and other heavy local work allowed by that repository.

## Startup model

RDC availability has priority over development-environment preparation.

The workflow must follow this order:

1. Restore RDC identity/state.
2. Install and start RDC.
3. Pass RDC health verification.
4. Enter the long-lived keepalive step, start heavy toolchain prewarm **in the background**, and keep that parent step alive.
5. Keep RDC online while prewarm continues.

Never delay RDC connectivity just to install build/media dependencies.

The automatic prewarm is managed by:

- `scripts/agent-prewarm.sh`
- `scripts/agent-bootstrap.sh --full`
- canonical readiness state: `~/.cache/agent-runner-kit/prewarm.env`
- canonical prewarm log: `~/.cache/agent-runner-kit/prewarm.log`
- toolchain compatibility/version rules: `scripts/agent-lib.sh`

Check readiness with:

```bash
./scripts/agent-run.sh status
```

If prewarm is still running, continue with work that only needs already-available tools. A stale or outdated READY marker is invalidated automatically when the toolchain version changes or a required command is missing.


## Runner lifetime and handoff safety

The nominal lifecycle is **330 minutes from runner initialization**, not 330 minutes from keepalive start and not the theoretical 6-hour GitHub ceiling. The job timeout is 350 minutes, deliberately leaving 20 minutes for checkpoint packaging, successor handoff, RDC shutdown, and state persistence. Never plan work against the hard platform limit.

At the start of every substantial task, and again before any operation expected to take more than a few minutes, run:

```bash
./scripts/agent-run.sh status
```

If GitHub connector access is available, also inspect the current **Remote Desktop Commander Lab** workflow run. Prefer the earlier of the local lifecycle deadline and GitHub's hard job deadline; live workflow/run timestamps are authoritative when they disagree with assumptions.

Interpret the local runtime state strictly:

- `SAFE`: more than 20 minutes remain; normal work is allowed. Do **not** abandon a task merely because 30–60 minutes remain.
- `RESTART_WINDOW`: 1–20 minutes remain; finish only the current atomic operation, push durable work, and rotate to a fresh runner.
- `RESTART_REQUESTED`: an agent already requested a clean rotation; do not start new work.
- `HANDOFF_DUE`: stop using this runner and move to its successor.

The keepalive loop automatically enters a clean handoff in the final 20 minutes: it creates a checkpoint, confirms/queues a successor using the existing workflow, then lets workflow finalizers persist RDC state. An agent with shell access can request the same path with `./scripts/agent-run.sh restart`; the command refuses early restarts while the runner is still SAFE unless `--force` is explicit. This checkpoint is not a substitute for pushing durable work to GitHub.

Manual checkpoint:

```bash
./scripts/agent-run.sh checkpoint manual
```

Automatic checkpoints include tracked working-tree diffs, unpushed commits (when an upstream exists), Git metadata, and only the **names** of untracked files. Untracked file contents are intentionally excluded to reduce secret-leak risk. The workflow encrypts the latest checkpoint with `RDC_STATE_KEY` before uploading it as a short-retention Actions artifact.

## Remote Desktop Commander call budget

RDC tool calls are a limited resource. Minimize them aggressively.

Preferred behavior:

- Batch related shell operations into one `start_process` call.
- Prefer one compound command that gathers status, reads several small files, performs searches, and reports results over many tiny calls.
- Use `scripts/agent-run.sh prepare ...` instead of separately checking tools, cloning, fetching, checking out, and gathering repository context.
- Use `scripts/agent-run.sh status` instead of individually checking each prerequisite.
- Prefer shell-native batching such as `find`, `rg`, `git`, `jq`, and short Python scripts when they reduce remote calls.
- Use subsequent RDC calls only when the previous command genuinely needs follow-up input/output.
- Do not repeatedly query static machine information already emitted by `agent-doctor.sh`.

A typical repository task should use the project-aware one-call path:

```bash
./scripts/agent-run.sh work --repo <url> --ref <branch>
```

or, for a PR with one or more targeted tests:

```bash
./scripts/agent-run.sh work --repo <url> --pr <number> --test tests/path/to/test.ts
```

Project-specific setup and test commands do **not** belong in this Lab repository. The target branch owns them in `.github/agent-lab/runner.sh`. The Lab exports a persistent, secret-free `AGENT_PROJECT_CACHE_DIR` outside the workspace; the workflow restores/saves that cache plus the npm download cache between ephemeral runners. This keeps dependency/test harness preparation branch-specific while avoiding repeated downloads and repeated RDC setup calls.

## Heavy workloads

This Lab is the preferred place for heavy local work when the target repository permits it, including:

- large TypeScript/Node builds;
- native compilation;
- targeted or repository-local test runs;
- static analysis and linting;
- media processing with FFmpeg/ImageMagick;
- database inspection;
- large source searches;
- debugging with build tools and system diagnostics;
- dependency-heavy reproduction work.

Do not run heavy work on production Railway merely because it is reachable. Production is not the development workstation.

## Toolchain policy

The background full prewarm installs a broad common toolchain once per runner lifetime. Standard tools should not be reinstalled manually through RDC.

The standard prewarm covers:

- Git/GitHub CLI, curl/wget, jq/yq;
- ripgrep, fd, fzf, tree, rsync, zip/unzip;
- Python + venv/dev tooling;
- build-essential, CMake, Ninja, pkg-config, Clang and GDB;
- Git LFS and common native headers;
- shellcheck, sqlite, network/process diagnostics;
- FFmpeg and ImageMagick;
- hosted Node.js/npm recovery when PATH is incomplete.

Large or uncommon SDKs remain on-demand, for example Android SDK, Rust toolchains, JDK variants, Playwright browser bundles, database servers, or project-specific SDKs. Install those only when a task actually requires them.

## Workspace safety

- Work under `~/agent-workspaces` unless a task requires another path.
- `agent-workspace.sh` refuses to overwrite a dirty workspace unless `--force` is explicit.
- Never assume changes on the runner are durable. Push durable changes to the correct GitHub branch.
- Do not commit RDC identity files, tokens, device state, secrets, or files from `~/.desktop-commander-device`.
- Do not modify the stable Tailscale Exit Node repository from this Lab unless the user explicitly asks.
- Preserve the RDC handoff/watchdog lifecycle unless the task specifically concerns it.

## Validation

For changes to this Lab itself:

1. Syntax-check changed shell scripts with `bash -n`.
2. Run the relevant helper in a bounded smoke test.
3. Verify RDC remains healthy.
4. Confirm prewarm status reaches `READY` or reports an actionable `FAILED` state.
5. Confirm existing unrelated working-tree changes were not overwritten.

Prefer direct, deterministic checks over adding new workflows solely for validation.
