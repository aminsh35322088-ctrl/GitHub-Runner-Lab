# AGENTS.md — GitHub Runner Lab

## Purpose

This repository runs an ephemeral GitHub-hosted Ubuntu machine that stays reachable through Remote Desktop Commander (RDC). Treat it as a disposable **heavy-work execution environment** for coding agents: source inspection, large builds, dependency-heavy debugging, media tooling, targeted tests, compilers, and other work that should not run on constrained production services.

The machine is temporary. Durable code belongs in GitHub, not only on the runner filesystem.

## Precedence

1. Read this file before using the Lab.
2. Before changing or testing another repository, read that target repository's own `AGENTS.md`, `CLAUDE.md`, or equivalent instructions.
3. The target repository's explicit policy overrides this Lab's defaults.
4. Never infer a target repository's CI, deployment, dependency, or test policy from this Lab. Follow the target repository's own instructions and Agent Lab contract.

## Agent entrypoint and incident reporting

At every connection run `agent-lab onboard` (or `./scripts/agent-run.sh onboard`
from the Lab checkout), then read the indicated instructions. This prints the
live lifecycle, `safe_work_seconds`, recovery state and supported commands.
The workflow installs `agent-lab` in `~/.local/bin` and copies this guide to
`~/AGENTS.md` for clients that discover home instructions. Clients that ignore
instructions cannot be forced to read them; shell/SSH/RDC agents share the CLI.

Before a long operation compare its expected duration with `safe_work_seconds`.
The nominal handoff time includes a 20-minute drain window; do not interpret all
`remaining` seconds as time available to start new work.

Report reproducible **Lab** defects to this repository using:

```bash
agent-lab issue --category TOOLCHAIN --key missing-cmake \
  --title 'CMake unavailable after prewarm' \
  --steps 'Run cmake --version after READY; reproduce in a fresh shell' \
  --expected 'A version is printed' --actual 'Command not found' --occurrences 2
```

Use `--dry-run` to inspect the sanitized report. Do not paste raw logs, source,
credentials, private project names or personal information into public Issues.
Summarize relevant evidence. The helper redacts common credentials but cannot
recognize every secret. Project test failures belong to their project, not Lab.
The stable category/key deduplicates open Issues; repeated identical evidence is
suppressed and new evidence becomes a comment. API errors preserve a local pending
report, included in encrypted checkpoints. `agent-lab issue --flush` retries via a
fresh duplicate check. Keepalive also retries pending reports. No blind POST retry.
The local lock serializes agents on this runner; independent runners can still race.
Repeated default runner-validation failures are reported automatically; custom
fault-injection manifests/binaries are excluded. Validation results never depend
on successful Issue delivery.

Capture reproduction evidence with `agent-lab environment --workspace PATH
--output FILE`, and compare on a replacement runner with `--compare FILE`.
Differences return a nonzero exit code. Latest stable installation remains the
default: this detects drift, it does not claim byte-identical reconstruction of
old hosted images or download availability of old tools. Commit the evidence file
with the task when exact historical comparison is needed.

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
- canonical toolchain/acceptance contract: `config/runner-toolset.json`
- manifest query/readiness bridge: `scripts/lab_toolset.py` and `scripts/agent-lib.sh`

Check readiness with:

```bash
./scripts/agent-run.sh status
```

If prewarm is still running, continue with work that only needs already-available tools. READY is written only after full bootstrap, manifest-driven command readiness, and `validate runner quick` succeed. A stale or outdated READY marker is invalidated automatically when the toolchain version changes or a required command is missing.


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

Automatic checkpoints cover Lab-managed clones and linked worktrees plus scratch repositories explicitly registered with `./scripts/agent-run.sh workspace adopt PATH`. They contain a full Git bundle, staged and unstaged patches, metadata, and allowlisted untracked source/configuration files. Secret-like content in tracked dirty patches fails closed; unsafe/unrecognized untracked files are excluded. Every stored file is checksummed. Recovery verifies the exact file set, materializes repositories into a fresh recovery root, re-registers them for future checkpoints, and reapplies a repository-local Git author identity when one can be derived safely. The workflow authenticates and encrypts the latest checkpoint with `RDC_STATE_KEY`, saves the current encrypted snapshot on the dedicated `agent-checkpoints` branch, and also uploads a short-retention artifact.

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

For full validation, use `./scripts/agent-run.sh validate <workspace>`. The target hook must implement `prepare`, `check`, `full`, `test`, and `clean-materialized`. On a failed `full`, it may write newline-delimited failing selectors to `$AGENT_JOB_OUTPUT_DIR/failed-tests.txt`; the Lab reruns only those selectors for diagnosis, preserves the original failure, stores bounded stage logs plus machine/human summaries, and requests cleanup after normal completion or caught `SIGINT`/`SIGTERM`. A forced `SIGKILL` cannot run cleanup. Shared resources are serialized only when the target repository declares an `exclusive_group` in its Agent Lab contract.

Validate the Lab runner separately from project policy:

```bash
./scripts/agent-run.sh validate runner quick
./scripts/agent-run.sh validate runner full
./scripts/agent-run.sh validate full <workspace>
```

`runner quick` is the prewarm readiness gate and covers host/toolchain/network/RDC acceptance plus native/CMake/Node/Python/Git smoke operations. It also compiles/runs small Go, Rust/Cargo, and Java programs and lints a sample Actions workflow with actionlint. `runner full` adds isolated Docker and media smoke tests. `validate full <workspace>` requires full runner validation before invoking the target repository's project contract. Treat `HOST`, `TOOLCHAIN`, `NETWORK`, `RDC`, `DOCKER`, `MEDIA`, `PROJECT`, and `CLEANUP` as stable failure categories when triaging summaries.

Use `./scripts/agent-run.sh shell-help` before dropping to a raw shell. On a healthy Runner, GitHub authentication is transparent: use normal `gh`, HTTPS `git`, and `docker` commands for `ghcr.io` from any fresh RDC/Agent shell and do not expect a GitHub PAT in the agent's environment; GitHub CLI/Git/Docker authentication is already configured. `agent-run.sh github` remains only as a backward-compatible wrapper. Do not run raw `npm ci` against a workspace whose `node_modules` is a shared/materialized link.

Run tests through Remote Desktop Commander. Use managed jobs when a test must survive a disconnected shell, needs a timeout/report, or can spawn descendants:

```bash
job="$(./scripts/agent-run.sh job start --cwd "$PWD" --timeout 1800 -- npm test)"
./scripts/agent-run.sh job wait "$job"
```

Managed jobs start with a clean, isolated HOME. Host managed jobs receive only the non-secret paths to GitHub CLI's stored auth and a dedicated credential-helper-only Git config, so normal `gh`/HTTPS `git` work without token environment variables. Pass other required non-secret fixtures explicitly with `--pass-env NAME`; credential-shaped names, GitHub auth-routing paths, and Git configuration overrides (including `GIT_CONFIG_*`, `GIT_DIR`, and askpass variables) are rejected. Host managed jobs should declare GitHub need explicitly: `job start --grant github ...` (for example a job that runs `git push` or `gh`). Today a host job without `--grant` still works but prints a warning and records `github_access: implicit` in its report; set `AGENT_JOB_GITHUB_ENFORCE=enforce` to make ungranted host jobs run without the managed GitHub credential paths. Foreground `gh`, `git` and `docker` in an RDC/Agent shell are unaffected. `--grant` is refused for container and `--untrusted` jobs. Container jobs intentionally do not inherit host GitHub credentials, default to `--network none`, drop Linux capabilities, and apply CPU, memory, swap, and PID limits. Completed reports and dependency caches older than 14 days are pruned only while no managed job is active.

**Security boundary:** this is environment/configuration hygiene, not a hostile-code sandbox. Host jobs run as the same Unix user as the Runner and can potentially read that user's GitHub auth files or inspect same-UID processes. Do not run untrusted repository code in host managed jobs; use `job start --untrusted --image IMAGE` and review outputs before any trusted host-side GitHub operation. `AGENT_JOB_GITHUB_ENFORCE=enforce` removes the managed credential paths for jobs without an explicit grant; it does not provide OS-level isolation from the Runner account.

For untrusted repository code, require `job start --untrusted --image IMAGE`: never run it directly in the RDC shell or a trusted host job. Host job CPU/RAM values are admission reservations only; use container jobs when hard limits matter. Keep GitHub commits and pushes in a separate trusted host step after reviewing isolated job output. Do not pass credential-like variables with `--pass-env`.

## Sigillo credential management

Sigillo is the Lab's source of credentials for trusted agent tasks, including the Agent's GitHub provider credential and other service API tokens. Before using Sigillo, read [`skills/sigillo/SKILL.md`](skills/sigillo/SKILL.md).

The ephemeral runner authenticates to Sigillo through the `SIGILLO_API_TOKEN` GitHub Actions secret. The workflow validates it with `sigillo me`, stores it in `~/.sigillo/config.json` with owner-only permissions, and verifies the saved credential. The config is recreated for each runner generation; never checkpoint, cache, commit, copy or manually restore it.

The Sigillo project configured in `.github/workflows/rdc-lab.yml` must contain `GITHUB_API_TOKEN` in the `prod` environment. It must be a classic PAT with GitHub CLI minimum scopes `repo`, `read:org`, and `gist`; this Lab also requires `write:packages` for its GHCR credential helper. The runner uses this credential only to bootstrap GitHub CLI authentication: it injects the value with `sigillo run`, pipes it to `gh auth login --with-token` on stdin, removes it from the installer process environment, then verifies GitHub API identity, HTTPS Git access, package scope, and the GHCR credential helper.

Other provider credentials belong in Sigillo and should be injected only into the specific trusted command that needs them. Never put credential values in arguments, source files, shell history, logs, GitHub outputs, issues, checkpoints, or caches. Never use `sigillo run` output as a secret transport.

**Keep GitHub Actions API authentication independent.** Workflow-control operations such as run listing, cancellation, dispatch, watchdog recovery, and README status publishing use the built-in `github.token` with explicit job permissions (or the optional `ACTIONS_WATCHDOG_TOKEN` override). They must not depend on Sigillo or on `GITHUB_API_TOKEN`.

Agent rules:

- Start with `sigillo me`, then use `sigillo projects` and `sigillo environments --project PROJECT_ID` to discover scope. Always use the actual project ID, not a display name.
- Use `sigillo secrets --project PROJECT_ID --env SLUG` to inspect names/metadata only. Never print secret values into a model/tool output, GitHub log, issue, summary, or chat.
- Prefer `sigillo run --project PROJECT_ID --env SLUG -- COMMAND` to inject secrets into a trusted child process without writing a dotenv file.
- Never read `.env` files or any file under `~/.sigillo/` directly. Use CLI status commands; never inspect the config with `cat`, `jq`, `grep`, or custom scripts.
- Never put a token in command-line arguments, shell history, source files, process output, a GitHub Actions output, a checkpoint, or a cache. Do not use `--disable-redaction` to reveal values.
- Sigillo redaction is defense in depth, not a security boundary. The child receives real values, and a process running as the same runner user can potentially request any credential allowed by the Sigillo token. Do not run untrusted repository code in the trusted RDC shell or a trusted host job just because it is wrapped in `sigillo run`; use the isolated untrusted container path.
- Do not create, rotate, broaden, or revoke Sigillo tokens or project secrets unless the user explicitly requests. Prefer the narrowest project/environment scope that supports the requested task.

The **Sigillo smoke test** workflow-dispatch action checks Sigillo authentication and project visibility without starting the persistent runner.

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
- Go, Rust/Cargo, and a headless Java JDK;
- actionlint and Goss resolve the latest official GitHub release at install time and verify the asset SHA-256 digest; Node.js and the RDC npm package use their latest stable releases; keep GitHub Actions pinned to immutable commit SHAs;
- build-essential, CMake, Ninja, pkg-config, Clang and GDB;
- Git LFS and common native headers;
- shellcheck, shfmt, sqlite, PostgreSQL/Redis/MariaDB clients and protobuf compiler;
- network diagnostics including Nmap, MTR, iperf3, tcpdump, traceroute, whois and socat;
- media/document tools including FFmpeg, ImageMagick, MediaInfo, SoX, MKVToolNix, libvips and Poppler;
- hosted Node.js/npm recovery when PATH is incomplete.

The reverse-engineering toolkit (REA) is the exception: the background prewarm automatically runs the repository's official `scripts/install-rea.sh` on every runner generation, after RDC is healthy. Prewarm does not report `READY` until the installer, REA capability check, and provider smoke tests succeed. This shared installation is available to every model/agent using the runner account; it is not installed separately per client.

## Runner Tool Center

The Runner has one local catalog for installed tools. It is a discovery layer over the real Shell, not an RPC service.

Start with intent-based search instead of searching the web or guessing package names:

agent-lab tools search "inspect APK or Android package"
agent-lab tools search "debug packet loss and TCP throughput"
agent-lab tools search "convert or inspect video and audio"
agent-lab tools info TOOL_ID
agent-lab tools list
agent-lab tools check
agent-lab tools report
agent-lab tools index

The catalog includes aliases, task keywords, exact command names, examples, provider ownership, and live availability. Use listed commands directly in Shell. REA-managed capabilities must go through rea; never run rea setup or add MCP registrations. /opt/runner-tools is the common root for custom binaries, REA providers, runtimes, registry and caches. APT-managed programs remain in standard system locations and are indexed in the same catalog.

Before installing a tool, check the local catalog. Do not duplicate a utility under a second package manager. Add or change catalog entries in config/runner-tools.json; add package provisioning to config/runner-toolset.json and keep readiness checks aligned.

## REA usage on every runner

After `agent-lab onboard`, run `agent-lab status` and require `REA=READY` before reverse-engineering work. If prewarm is still `RUNNING`, continue only with tasks that do not need REA and check again later. If status is `FAILED`, `OUTDATED`, or `REA=MISSING`, inspect the prewarm log and repair through the official installer from the Lab checkout:

```bash
./scripts/install-rea.sh
source "${REA_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/rea}/providers.env"
rea capabilities --format json
rea doctor --provider ghidra --format json
rea doctor --provider jadx --format json
```

Use the REA CLI as the default evidence-producing interface for relevant binary, Android, firmware, process, browser, and network reverse-engineering tasks. Inspect `rea capabilities --format json` and the command's help before choosing an operation; do not invent commands or claim unsupported provider coverage. The Lab's `agent-lib.sh` loads `providers.env` for non-login shells, so `rea` is available to shell-capable agents without client-specific setup.

Do not run `rea setup` automatically. It edits client-specific MCP/agent configuration; the shared CLI is intentionally provisioned once for the runner and can be invoked by any connected model through shell commands. Do not claim an MCP registration exists unless it has been separately configured and verified.

## Workspace safety

- Work under `~/agent-workspaces` unless a task requires another path. If valuable work must live elsewhere, immediately register the repository with `./scripts/agent-run.sh workspace adopt PATH`; unregistered arbitrary paths are not checkpointed.
- `agent-workspace.sh` refuses dirty, divergent, or unpublished work instead of resetting it. Use another explicit `--dir` when the existing workspace must be preserved.
- Fresh and recovered Lab workspaces use repository-local Git author metadata; never solve identity problems by placing tokens or credentials in Git config.
- If a remote-tracking ref is missing or corrupt, use `./scripts/agent-run.sh git-sync WORKSPACE --branch BRANCH`. The helper verifies the remote branch, repairs only safe loose-ref corruption, never resets/rebases the worktree, and fails closed rather than editing `packed-refs` directly.
- Never assume changes on the runner are durable. Push durable changes to the correct GitHub branch.
- Do not commit RDC identity files, tokens, device state, secrets, or files from `~/.desktop-commander-device`.
- The workflow bootstraps GitHub CLI from Sigillo's `GITHUB_API_TOKEN` once, then future shells use GitHub CLI's stored authentication, the Git credential helper, and the `ghcr.io` Docker credential helper. Never copy the PAT into a job environment, Docker config, checkpoint, cache, repository config, URL, or command line; Agents should use normal `gh`/`git`/`docker` commands instead of reading the secret.
- Do not modify the stable Tailscale Exit Node repository from this Lab unless the user explicitly asks.
- Preserve the RDC handoff/watchdog lifecycle unless the task specifically concerns it.

## Validation

For changes to this Lab itself:

1. Through Remote Desktop Commander, run `bash -n` for changed shell scripts, `python3 -m py_compile` for changed Python, and `git diff --check`.
2. Run `python3 -m unittest discover -s tests -v`.
3. Run `./scripts/agent-run.sh validate runner quick`; for runner/toolchain changes also run `./scripts/agent-run.sh validate runner full`.
4. Exercise the relevant fault-injection path and confirm the stable failure category is reported without a false green.
5. Verify RDC remains healthy and prewarm reaches `READY` or reports an actionable `FAILED` state.
6. Confirm existing unrelated working-tree changes were not overwritten.

Prefer direct, deterministic checks over adding new workflows solely for validation.
