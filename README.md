# GitHub Runner Lab

**GitHub Runner Lab is a disposable, self-healing development machine for AI coding agents.**

It turns a GitHub-hosted Ubuntu Actions runner into a remotely reachable, prewarmed execution environment where an agent can inspect repositories, build software, run tests, reproduce bugs, use native/media tooling, and perform other heavy development work without pushing that load onto production infrastructure.

The Lab is intentionally **project-agnostic**. It provides the machine, access, lifecycle, isolation, authentication, checkpointing, and execution primitives; the target repository remains responsible for its own build/test policy.

> The runner is temporary. Durable code belongs in GitHub. The Lab is an execution environment, not a production server or a long-term source-of-truth filesystem.

## Why this exists

AI coding agents are often good at reasoning about a repository but constrained by the environment in which they run. Large builds, native toolchains, Docker tests, dependency-heavy debugging, media processing, and long-running diagnostics need a real machine with predictable tooling and enough resources.

GitHub Runner Lab provides that machine while keeping it disposable and recoverable:

- **Remote agent access** through Remote Desktop Commander (RDC) and Tailscale SSH.
- **Fast startup** by bringing remote access online before heavy toolchain prewarm completes.
- **Prewarmed development tooling** for Node, Python, Go, Rust, Java, C/C++, GitHub Actions linting, Docker, diagnostics, and media workflows.
- **Transparent GitHub access** for normal `gh`, HTTPS `git`, and GHCR operations from trusted host sessions.
- **Managed jobs** with timeouts, durable results, bounded logs, cancellation, concurrency control, and resource admission.
- **Container isolation** for untrusted code with no inherited GitHub credentials, no Docker socket, no network by default, and enforced CPU/RAM/PID limits.
- **Workspace checkpointing** so valuable uncommitted work can survive runner rotation.
- **Automatic lifecycle handoff** before the GitHub-hosted job reaches its hard timeout.
- **Project-owned contracts** so this repository never hardcodes another project's setup or test policy.
- **Self-validation and watchdog recovery** so the Lab can detect broken host/toolchain/access state instead of silently presenting a stale green environment.

## Optional: Runner Credential Vault

The Runner Credential Vault is a separate proxy-only integration. Unlike Sigillo, it does not inject provider secret values into a child process; the raw credential remains in the Vault Worker while an owner-approved API request is proxied and filtered.

For non-GitHub, non-Tailscale credentials (for example Cloudflare or Railway tokens), an agent can use a
separate Cloudflare Worker vault to make the API call without receiving the raw token, or ask the owner to register
a missing credential through a one-time link.

The Lab-side integration is optional and needs no configuration in this repository. At the start
of every run, the `runner-lab` job looks for a deployed Vault by reading `deployment.json` from
the root of the Vault repository (`GitHub-runner-vault` under the same GitHub account). When a
deployment is found, the job automatically downloads the pinned client, exchanges its GitHub OIDC
identity for a short-lived Vault session, and reads the credential registry after RDC health and Tailscale SSH are ready. Vault network work is time-bounded and cannot delay or block either access path.
No runner token, repository ID, owner ID or manual Vault policy entry is required. The Vault validates
the deployment-pinned GitHub owner, exact `main/.github/workflows/rdc-lab.yml` identity and allowed
event, then atomically pins the first valid repository ID in D1. Later runs must match that pin.

The existing **Remote Desktop Commander Lab → Vault smoke test** action performs the same OIDC/session
flow and additionally checks the saved GitHub and Cloudflare credentials through read-only provider
API calls. Response bodies stay in runner-temp files; logs and summaries contain only pass/fail markers.
The normal runner startup also connects automatically after RDC and Tailscale are ready. A missing,
slow or unavailable Vault degrades Vault access only and does not block either access path.

To deploy a Vault, use the Vault's own setup wizard at
https://aminsh35322088-ctrl.github.io/GitHub-runner-vault/ — it walks through the one supported
deployment, and the Lab needs nothing at all: after each successful deploy, the Vault's deploy
workflow publishes `deployment.json` (the Worker URL and the deployed Vault commit) back to the
Vault repository, and that file is all the Lab reads. Before trusting it, the Lab still validates
that the URL is an HTTPS origin with no path, query or credentials and that the client revision
is an immutable 40-character commit SHA, so the client stays pinned to a reviewed revision — the
pin is now maintained by the Vault's deploy instead of by hand. If no Vault is deployed (no
readable `deployment.json`), the Lab skips Vault integration and nothing else changes. To disable
a deployed Vault again, delete or rename `deployment.json` in the Vault repository (or delete
the Vault); the Lab stops connecting on its next run. Upgrading the Vault automatically upgrades
what the Lab uses on its next run, because discovery always reads the latest published
deployment. The existing AGENT_GITHUB_TOKEN remains the separate trusted
Agent GitHub CLI/Git/GHCR bootstrap credential; the Vault proxy does not replace Git smart-HTTP
or gh authentication. The same secret is available to the pinned-client download step only
as a scoped fallback for private-source access or anonymous API rate limits.

## System model

```text
AI coding agent / operator
        │
        ├── Remote Desktop Commander
        └── Tailscale SSH (GitHub-Lab)
                    │
                    ▼
        Ephemeral GitHub-hosted Ubuntu runner
                    │
        ┌───────────┼────────────┐
        │           │            │
   Agent tooling  Workspaces  Managed jobs
        │           │            │
        │           │      ┌─────┴─────┐
        │           │      │           │
        │           │   trusted     untrusted
        │           │   host job   container job
        │           │
        └───────────┼─────────────────────┐
                    │                     │
             encrypted checkpoint   target repository
                    │                 contract/policy
                    ▼
            next runner generation
```

The runner is deliberately replaceable. Continuity comes from GitHub plus authenticated/encrypted checkpoints, not from assuming the current VM will stay alive.

## Design principles

1. **Remote access first.** RDC is restored, started, and health-checked before the expensive development toolchain is prewarmed.
2. **Disposable compute, durable work.** The VM may disappear; pushed commits and verified checkpoints must not.
3. **Target policy wins.** The Lab supplies generic execution primitives. A target repository defines how it should be prepared and validated.
4. **Fail closed on credentials and unsafe state.** Authentication, checkpoint integrity, workspace repair, and untrusted execution prefer refusal over silent fallback.
5. **Separate trusted and untrusted execution.** Trusted host work can use the authenticated runner identity; untrusted code must use the container path.
6. **Rotate before failure.** The Lab hands work to a successor before the GitHub job's hard timeout instead of relying on the theoretical platform ceiling.
7. **Minimize agent friction.** Common status, workspace, validation, job, GitHub, and recovery operations are exposed through one entry point: `scripts/agent-run.sh`.

<!-- RDC-LAB-STATUS:START -->

## ⏱️ Live Runner status

| Item | Value |
| :--- | :--- |
| Runner state | 🟢 RDC verified · keepalive running |
| Agent work mode | 🟢 SAFE — normal work window |
| Status freshness | ✅ current as of 2026-11-09 09:57:42 UTC |
| Last checked | 2026-11-09 09:57:42 UTC |
| Runner/job started | 2026-11-09 09:56:40 UTC |
| Keepalive started | 2026-11-09 09:57:23 UTC |
| Runner age | 1 min |
| Runner lifecycle remaining | 328 min |
| Auto restart threshold | 20 min remaining |
| Nominal handoff | 2026-11-09 15:26:40 UTC |
| Hard job timeout | 2026-11-09 15:46:40 UTC |
| Planned timeout headroom | 20 min |
| Successor already queued | — |
| RDC verification success | 100% (2 samples) |
| Keepalive completion | — |
| Handoff ≤15 min | 100% (1 samples) |
| Median handoff gap | 0.8 min |
| Run details | [Open current run](https://github.com/aminsh35322088-ctrl/GitHub-Runner-Lab/actions/runs/42597110741) |

> Refreshed on workflow events and about every 10 minutes. For the exact local clock while connected, run `./scripts/agent-run.sh status`. The lifecycle clock starts during early runner setup: handoff is planned at 330 minutes with a 350-minute hard job timeout. Normal work remains SAFE until the final 20 minutes, when the current run checkpoints and rotates to a fresh runner. Reliability percentages are measured from the corresponding workflow steps in up to the last 12 completed runs; handoff reliability means the next run started within 15 minutes.

<!-- RDC-LAB-STATUS:END -->

## Primary workflows

The repository has three GitHub Actions workflows with separate responsibilities:

| Workflow | Responsibility |
| --- | --- |
| `.github/workflows/rdc-lab.yml` | Creates the runner generation, restores RDC state, establishes remote access, prewarms tooling, keeps the environment alive, checkpoints work, and performs clean handoff. |
| `.github/workflows/rdc-watchdog.yml` | Recovery backstop when the long-lived Lab workflow is missing or unhealthy. |
| `.github/workflows/repository-heartbeat.yml` | Keeps scheduled automation eligible and runs the repository's lightweight regression/syntax validation on normal code changes. |

## Quick start

Repository setup requires these secrets:

| Secret | Purpose |
| --- | --- |
| `RDC_STATE_KEY` | Encrypts/authenticates persisted RDC identity and Agent checkpoints. Use at least 32 random characters. |
| `AGENT_GITHUB_TOKEN` | Bootstraps the agent's persistent GitHub CLI/Git/GHCR authentication. A classic PAT is used when package publishing is required. |
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client ID for the ephemeral `GitHub-Lab` node. |
| `TS_OAUTH_SECRET` | Tailscale OAuth secret paired with the client ID. |
| `SIGILLO_API_TOKEN` *(optional)* | Sigillo API bearer token for non-interactive CLI authentication. Store only as an Actions secret. |
| `SIGILLO_API_URL` *(optional variable)* | HTTPS origin for a self-hosted Sigillo instance; defaults to `https://sigillo.dev`. |

Then open **Actions → Remote Desktop Commander Lab → Run workflow**, leave **Start / Resume** selected, and run it. On first use, complete the RDC browser authorization shown by the workflow. Future runner generations restore the encrypted RDC identity automatically.

To move the Lab to another Desktop Commander account, run the same workflow with **Connect a new RDC account**. The account switch validates the replacement identity before overwriting saved state; a failed switch keeps the previous encrypted identity available for recovery.

### Optional Sigillo setup

Create a Sigillo API token and save it in **Repository → Settings → Secrets and variables → Actions** as `SIGILLO_API_TOKEN`. Prefer a project-scoped token restricted to only the environments agents need. If agents need all environments, allow only the required slugs such as `dev`, `preview`, and `prod`.

For a self-hosted Sigillo instance, add the repository variable `SIGILLO_API_URL` with its HTTPS origin. Leave it unset for `https://sigillo.dev`.

The existing `runner-lab` job performs non-interactive authentication before RDC starts: it validates the token with `sigillo me`, stores it using `sigillo login --scope /`, locks the config to owner-only permissions, then verifies the saved credential again. No browser/device login is required. The config is created for each ephemeral runner generation from the Actions secret; it is not committed, cached, or checkpointed.

Sigillo remains optional. A missing, invalid, unreachable, or misconfigured token disables only Sigillo and must not block RDC or Tailscale. See [docs/sigillo.md](docs/sigillo.md).

## Access model

### Remote Desktop Commander

RDC is the primary agent-access path and has startup priority. Heavy package installation never blocks initial RDC connectivity. The workflow pins the Desktop Commander runtime and supervises its remote channel so health is reported only while the connection remains usable.

### Tailscale SSH

After RDC is healthy, the runner joins the Tailnet as an **ephemeral** node named `GitHub-Lab`, tagged for SSH. It does not advertise itself as an Exit Node.

The host provides compatibility for clients and agents that default to `root`:

```bash
ssh root@GitHub-Lab
```

The Tailscale policy must authorize the requested SSH identity. Once authorized, the host-side `root` compatibility path re-executes the session as the normal GitHub runner user so files, toolchains, and credentials keep the expected ownership model.

Before handoff, the current generation explicitly logs out of Tailscale so the successor can reuse the `GitHub-Lab` MagicDNS name without accumulating duplicate live/stale nodes.

## Agent workflow

Agents should read [`AGENTS.md`](AGENTS.md) before using or changing the Lab. When working on another repository, that target repository's own instructions are authoritative.

Check the current machine and lifecycle before substantial work:

```bash
./scripts/agent-run.sh status
```

Get a machine-readable readiness snapshot:

```bash
./scripts/agent-run.sh ready
./scripts/agent-run.sh ready --workspace /path/to/workspace
```

Prepare a target repository in one call:

```bash
./scripts/agent-run.sh work --repo https://github.com/OWNER/REPO.git --ref main
```

or prepare an explicit branch/PR workspace:

```bash
./scripts/agent-run.sh prepare --repo https://github.com/OWNER/REPO.git --ref main
./scripts/agent-run.sh prepare --repo https://github.com/OWNER/REPO.git --pr 123
```

The Lab has no built-in target project. An installation that intentionally uses one default repository may set `AGENT_DEFAULT_REPO`; otherwise the target must be explicit.

For Sigillo work, agents should read the vendored [Sigillo skill](skills/sigillo/SKILL.md) and [Lab integration guide](docs/sigillo.md). The upstream skill can also be installed globally with `npx -y skills add remorses/sigillo -g -y`.

## Project contract

Project-specific setup, validation, dependency, and cleanup policy belongs in the **target repository**, not here.

A target branch can expose:

```text
.github/agent-lab/runner.sh
```

The hook owns project-level actions such as `status`, `prepare`, `check`, `full`, `test`, and cleanup. The Lab can then run the target's validation as one managed operation:

```bash
./scripts/agent-run.sh validate /path/to/workspace
```

Targets may also provide an optional declarative contract:

```text
.github/agent-lab/contract.json
```

Schema v1 can declare bootstrap requirements, required commands/pkg-config modules, minimum lifecycle and disk headroom, and an `exclusive_group` for host resources that must be serialized.

Example:

```json
{
  "schema_version": 1,
  "required_commands": ["cargo"],
  "minimum_lifecycle_seconds": 1800,
  "minimum_free_disk_gb": 20,
  "exclusive_group": "shared-build-resource"
}
```

This boundary is intentional: changing a project's build/test policy should not require changing the Lab itself.

## Managed execution

Long-running builds/tests should use managed jobs when they need timeout control, cancellation, durable output, process-tree handling, or continuity across a disconnected interactive shell.

```bash
job="$(./scripts/agent-run.sh job start --cwd "$PWD" --timeout 1800 -- npm test)"
./scripts/agent-run.sh job wait "$job"
```

Managed jobs provide a clean environment, durable JSON results, artifact indexing, bounded output, a termination grace window, approximate peak process-group RSS, per-workspace exclusion, and global concurrency/resource admission.

Trusted **host jobs** can use the authenticated GitHub CLI/Git credential paths, but their CPU/RAM values are admission reservations rather than kernel-enforced limits.

For untrusted target code, require a container job:

```bash
./scripts/agent-run.sh job start --untrusted --image IMAGE --cwd "$PWD" -- COMMAND
```

Untrusted containers default to no network, no inherited GitHub credentials, no Docker socket, dropped capabilities, a read-only root filesystem, bounded temporary storage, and enforced CPU/RAM/swap/PID limits. Credential-like variables are rejected from `--pass-env`.

## GitHub and GHCR authentication

`AGENT_GITHUB_TOKEN` is consumed during bootstrap rather than exposed as a normal agent environment variable. The runner configures:

- GitHub CLI authentication;
- HTTPS Git through the GitHub CLI credential helper;
- a `ghcr.io` Docker credential helper backed by the same stored GitHub CLI identity.

Fresh trusted RDC/Agent shells therefore use normal commands such as `gh`, `git fetch`, `git push`, `docker pull`, and `docker push` without needing `AGENT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` in their environment.

For GHCR publishing, the classic PAT must include `write:packages` in addition to the repository/organization scopes required by the intended work. The bootstrap path fails closed if the GitHub API identity, Git credential lookup, required package scope, or GHCR credential lookup cannot be verified.

The PAT is not intentionally copied into Docker config, repository config, checkpoints, caches, URLs, or command-line arguments.

## Sigillo secret manager

Sigillo is the Lab's optional CLI-based secret manager for trusted build/deployment commands. It is separate from GitHub authentication and the Runner Credential Vault.

| Credential path | Purpose | Security model |
| --- | --- | --- |
| `AGENT_GITHUB_TOKEN` bootstrap | GitHub CLI, HTTPS Git and GHCR access. | Independent GitHub credential-helper state. |
| Sigillo | Inject selected project/environment secrets into a child process. | API token lives in GitHub Actions Secrets and a private config for the current runner. `sigillo run` redacts matching child output by default. |
| Runner Credential Vault | Proxy owner-approved provider API requests. | Raw credential stays in the Vault Worker; only filtered API responses return to the runner. |

Sigillo redaction is not a hostile-code security boundary: the child receives real values, and a process running as the same runner user can potentially use the CLI to request whatever the API token is allowed to read. Use the proxy-only Runner Credential Vault when a credential must never enter the runner.

The workflow maps Actions secret `SIGILLO_API_TOKEN` to the CLI's `SIGILLO_TOKEN` variable. It optionally reads repository variable `SIGILLO_API_URL` and accepts only an HTTPS origin. It validates the bearer token before storing it because `sigillo login` saves a token but does not itself validate it. `SIGILLO_CLI_READY=true` is emitted only after a second `sigillo me` check. Failure is non-blocking.

Use project IDs, not display names, for explicit CLI operations:

```bash
sigillo me
sigillo projects
sigillo environments --project <PROJECT_ID>
sigillo secrets --project <PROJECT_ID> --env dev
sigillo run --project <PROJECT_ID> --env dev -- npm test
```

Never read `~/.sigillo/` or `.env` files directly, print secret values, or use `sigillo run` output as a secret transport. For agent rules and current CLI references, see [docs/sigillo.md](docs/sigillo.md) and [skills/sigillo/SKILL.md](skills/sigillo/SKILL.md).

## Lifecycle and handoff

The Lab does not try to stretch a GitHub-hosted job to the theoretical maximum runtime.

- Nominal runner handoff: **330 minutes after initialization**.
- Workflow hard timeout: **350 minutes**.
- Reserved cleanup/handoff headroom: **20 minutes**.

Runtime states are intentionally simple:

| State | Meaning |
| --- | --- |
| `SAFE` | More than 20 minutes remain; normal work is allowed. |
| `RESTART_WINDOW` | Final 1–20 minutes; finish the current atomic operation, make work durable, and rotate. |
| `RESTART_REQUESTED` | A clean rotation has already been requested; do not start new work. |
| `HANDOFF_DUE` | Stop using the current generation and move to its successor. |

During the final window the keepalive path checkpoints registered workspaces, confirms/queues a successor, ends cleanly, persists remote-access state, and lets the next runner generation take over.

An agent can request the same guarded path with:

```bash
./scripts/agent-run.sh restart
```

Early restart is refused while the runner is still `SAFE` unless an explicit force option is used.

## Checkpoint and recovery

Checkpoints protect valuable **uncommitted** work across runner replacement, but they are not a substitute for pushing durable commits to GitHub.

Lab-managed clones/worktrees are registered automatically. Valuable repositories outside the normal workspace root must be explicitly adopted:

```bash
./scripts/agent-run.sh workspace adopt /path/to/scratch-repo
```

A manual checkpoint can be requested with:

```bash
./scripts/agent-run.sh checkpoint manual
```

The checkpoint path captures verified repository state including Git bundles, staged/unstaged patches, metadata, and allowlisted untracked source/config files. It checksums the stored file set, fails closed on secret-like tracked dirty patches, excludes unsafe/unrecognized untracked files, authenticates/encrypts the snapshot with `RDC_STATE_KEY`, and persists the latest encrypted state outside the disposable VM.

Recovery materializes repositories into a fresh recovery root, verifies the snapshot before use, and re-registers restored workspaces for later checkpoints.

## Toolchain and readiness

The full background prewarm covers the common coding/debugging stack rather than every possible SDK:

- Git and GitHub CLI;
- Node.js/npm, Python, Go, Rust/Cargo, and a headless Java JDK;
- Version policy for the privileged runner path: Node.js is pinned to an exact LTS patch release at every `actions/setup-node` site, the Remote Desktop Commander npm package is pinned to an exact version whose registry tarball SHA-512 integrity is verified by `scripts/install-rdc.sh` before install, and the Tailscale action receives an exact release version — none of these float on `latest`. Bump the pins deliberately in one change (Node: newest LTS patch from nodejs.org/dist; RDC: `npm view @wonderwhy-er/desktop-commander dist-tags.latest dist.integrity`; Tailscale: latest stable release tag). `actionlint` and Goss still resolve GitHub’s latest release at install time and verify the release asset SHA-256 digest;
- ripgrep/fd/fzf, jq/yq and common shell utilities;
- C/C++ build tools, CMake, Ninja, pkg-config, Clang and GDB;
- Git LFS, SQLite, network/process diagnostics;
- FFmpeg and ImageMagick;
- Docker/BuildKit when supplied by the GitHub-hosted image.

Large specialized environments such as Android SDKs, Playwright browser bundles, database servers, and project-specific SDKs remain on-demand.

### Optional reverse-engineering toolkit (REA)

REA is an opt-in CLI/MCP toolkit for evidence-backed application, binary, process, Android, browser, firmware, and network-capture analysis. It stays outside core prewarm and startup, so installing it never delays RDC/Tailscale readiness.

Install it when a task needs reverse-engineering capabilities:

```bash
./scripts/install-rea.sh
source "${REA_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/rea}/providers.env"
rea --help
rea capabilities
rea doctor --provider ghidra --format json
rea doctor --provider jadx --format json
```

The provider-scoped Doctor commands verify the two engines installed by default. The unscoped `rea doctor --format json` is a full environment audit and can exit nonzero when intentionally caller-managed integrations (Hopper, IDA, Kuna, or an agent skill registration) are absent; that does not mean the installed providers are unhealthy.

**REA tracks npm's `rea-agents@latest` dist-tag, but an install never floats.** The installer resolves the dist-tag exactly once per run to an exact version, checks that version's npm `engines.node` range against its own pinned Node.js runtime (the installer carries Node.js 26.12.1 itself, independent of the workflow's `setup-node` version), downloads the registry tarball and verifies its SHA-512 `dist.integrity` before installing that exact version, and records the resolved version and integrity in `providers.env` (`REA_VERSION` / `REA_INTEGRITY`) so every install is auditable and reproducible after the fact. One honest limit: REA's own transitive npm dependencies are still resolved by npm at install time — pinning those would require a lockfile and is a documented follow-up, not part of this change. Supporting runtimes and providers are deliberately fixed to this tested baseline instead of trying to dynamically guess compatibility on every run:

| Component | Pinned version |
|---|---:|
| Node.js | 26.12.1 |
| Python toolchain | 3.13.x (newest installed Runner toolcache patch) |
| Ghidra + Temurin JDK | 12.1.4 + 21.0.13+9 |
| WABT | 1.0.42 |
| JADX headless MCP bridge | 0.7.1 |
| Apktool | 2.12.1 |
| mitmproxy | 12.2.3 |
| TShark (Ubuntu package) | Latest available Wireshark 4.x |
| Frida tools / Frida runtime | 14.11.0 / 17.25.2 |
| pwntools / Capstone / pyelftools / Unicorn | 4.15.0 / 5.0.9 / 0.33 / 2.1.2 |
| Unblob | 26.6.4 |
| Binwalk native / Python API | 3.1.0 / 3.2.0 |
| System Google Chrome | 156.0.8078.x (Runner image patch) |

Ghidra 12.1.4 is intentional: REA's current Ghidra bridge verifies against that release line; Ghidra 12.2 was rejected by the actual provider Doctor. Python 3.13.x is used for every provider virtual environment, selecting the newest installed Runner toolcache patch, so the pinned Unicorn 2.1.2 binary wheel is selected instead of compiling the multi-architecture QEMU source on Python 3.14. Capstone is fixed at stable 5.0.9 rather than accepting the 6.0.0 alpha that an unconstrained resolver can otherwise choose. Native Binwalk is built from its pinned upstream commit using Cargo; `RUST_FONTCONFIG_DLOPEN=1` avoids a build-time fontconfig development-package dependency while retaining the host runtime library. TShark is installed from the current Ubuntu package candidate and verified to be in the Wireshark 4.x release line. Chrome's 156.0.8078 patch component follows the Runner image update, while the browser compatibility branch is held constant.

The installer verifies SHA-256 for the pinned Node.js, Ghidra, Temurin, WABT, JADX, and Apktool archives (a cached archive is reused only after its checksum verifies) and SHA-512 integrity for the REA tarball. It saves provider paths in `providers.env`, including `REA_VERSION`, `REA_BINWALK_COMMAND`, `REA_UNBLOB_COMMAND`, `REA_JADX_MCP_JAR`, `REA_PWNTOOLS_PYTHON`, and the browser/mitmproxy command paths. `--dry-run` is strictly side-effect-free: it prints the plan and never sources an existing `providers.env`. Google Chrome 156.0.8078.x from the Runner image is reused (Playwright's Chromium downloader does not yet map this Ubuntu 26.04 image); the installer launches it using the Playwright Core bundled with the installed REA for a headless smoke test.

Selective options are available: `--skip-os-packages`, `--skip-ghidra`, `--skip-android`, `--skip-network`, `--skip-browser`, `--skip-python-tools`, `--skip-binwalk`, and `--dry-run`. The `--skip-binwalk` option leaves REA's firmware signature-inspection tool unavailable, but does not affect the rest of the toolkit. Python tooling is isolated in separate virtual environments. The installer's skip flags can be used for partial repairs without discarding previously installed provider paths.

`rea setup` is intentionally **not** run automatically: it can modify agent/MCP configuration. The installer does not register MCP clients or edit OpenCode/Codex settings. Commercial providers (IDA, Hopper, JEB) are not installed. Kuna remains caller-supplied. Process capture runs with the Runner account's permissions and is not a sandbox; only analyze targets you are authorized to inspect and treat untrusted targets as untrusted code.
The canonical machine/toolchain contract is [`config/runner-toolset.json`](config/runner-toolset.json). Bootstrap, readiness, doctor, and runner validation consume the same manifest so they do not maintain independent definitions of a healthy machine.

## Validation

The Lab validates itself separately from target-project policy.

```bash
./scripts/agent-run.sh validate runner quick
./scripts/agent-run.sh validate runner full
./scripts/agent-run.sh validate full /path/to/workspace
```

`runner quick` is the normal readiness gate. It checks the manifest-driven host/toolchain contract, disk/inode headroom, writable paths, GitHub connectivity, RDC heartbeat when present, and bounded native/CMake/Node/Python/Git smoke tests. It also builds and runs small Go, Rust/Cargo, and Java programs, then lint-checks a sample GitHub Actions workflow.

`runner full` adds Docker and media smoke tests. Docker validation uses an isolated local image with no registry dependency, no network, dropped capabilities, a read-only root filesystem, and bounded resources.

Validation reports are emitted in human- and machine-readable forms with stable failure categories:

`HOST`, `TOOLCHAIN`, `NETWORK`, `RDC`, `DOCKER`, `MEDIA`, `PROJECT`, and `CLEANUP`.

The Lab's regression suite lives under `tests/` and uses the Python standard library. For changes to this repository, [`AGENTS.md`](AGENTS.md) defines the authoritative validation procedure.

## Storage and cleanup

Docker/BuildKit pressure can be inspected without immediately deleting data:

```bash
./scripts/agent-run.sh docker-storage status
./scripts/agent-run.sh docker-storage check
```

Cleanup is opt-in and refuses to run while managed jobs are active:

```bash
./scripts/agent-run.sh docker-storage prune
./scripts/agent-run.sh docker-storage prune --apply
```

The first command is report/dry-run behavior; the second applies pruning of unused old BuildKit cache.

## Security boundaries

The main security boundaries are deliberate:

- The Lab runner is ephemeral and must not become the only copy of valuable source state.
- RDC persistence and Agent checkpoints are encrypted/authenticated with `RDC_STATE_KEY`.
- Tailscale joins as an ephemeral tagged SSH node and is **not** an Exit Node.
- Trusted host shells/jobs can use the runner's authenticated GitHub identity.
- Container jobs do not inherit host GitHub credentials.
- Untrusted code must not run directly in the trusted RDC shell or trusted host-job path.
- Secret-like values are rejected from untrusted `--pass-env` usage.
- Workspace/ref repair helpers verify remote state and refuse destructive recovery shortcuts.
- The Lab is independent from the separate stable Tailscale Exit Node system.

## Repository map

```text
.github/workflows/        runner lifecycle, watchdog, repository heartbeat
config/runner-toolset.json canonical host/toolchain acceptance contract
scripts/                  lifecycle, access, workspace, job, validation and recovery tooling
tests/                    Lab regression suite
AGENTS.md                  authoritative instructions for AI agents working in this Lab
README.md                  architecture, purpose and operator-facing overview
docs/sigillo.md            Sigillo operator, CLI and troubleshooting guide
skills/README.md           project-local agent skills index
skills/sigillo/SKILL.md    vendored upstream Sigillo agent skill
```

The main operator/agent entry point is:

```bash
./scripts/agent-run.sh
```

Key implementation areas include workspace preparation/registration, Git repair, lifecycle timing, encrypted checkpoint sync, managed jobs, GitHub auth, runner readiness, Docker storage checks, smoke validation, account switching, and clean Tailscale teardown.

## Non-goals

GitHub Runner Lab is **not**:

- a production application host;
- a persistent VM;
- a generic Tailscale Exit Node;
- a place to hardcode one target repository's build/test commands;
- a replacement for pushing durable Git history;
- a safe host-execution environment for arbitrary untrusted code;
- an excuse to run development builds/tests on production Railway services.

## Reviewing this project

For an architecture/code review, the most important questions are:

1. Can the Lab reliably establish remote access before expensive prewarm work?
2. Can a runner rotate without losing registered dirty workspace state?
3. Are credentials usable by trusted agent workflows without being unnecessarily exposed to jobs, containers, logs, caches, checkpoints, or command lines?
4. Are trusted host execution and untrusted container execution clearly separated and enforced?
5. Can lifecycle races create overlapping generations, duplicate Tailscale nodes, or broken RDC state?
6. Are watchdog/recovery paths idempotent and safe under cancellation or partial failure?
7. Does the target-repository contract keep project-specific policy out of the Lab core?
8. Do managed jobs correctly bound process lifetime, output, concurrency, cancellation, and resource admission?
9. Do readiness/self-tests detect stale or partially configured environments without producing false-green state?
10. Are GitHub Actions permissions, state branches, secret handling, and recovery mechanisms using the minimum practical privilege?

For agent-specific operating constraints and the exact self-validation procedure, continue with [`AGENTS.md`](AGENTS.md).

## Durable work, reproduction evidence and agent incidents

Run `agent-lab onboard` after connecting. All shell-capable models can discover
commands, recovery status and the usable time before the drain window. The guide
is also installed at `~/AGENTS.md`; automatic reading depends on the client.

Encrypted checkpoints preserve registered Git workspaces and pending incident
reports. Caches only accelerate preparation and can be evicted; after recovery use
the target repository's `prepare` hook to reconstruct dependencies. Never assume
an old `node_modules` symlink is usable after rotation. Cache keys now include the
actual Node version, OS/architecture and workflow attempt instead of a fixed Node
major. Project hooks still own lockfile-aware dependency materialization.

`agent-lab environment --workspace PATH --output FILE` records Lab/project SHAs,
actual tool versions and root lockfile/hook hashes. `--compare FILE` detects drift.
Project managed setup also writes `environment.json` alongside job output. Latest
tools remain the default; exact historical OS reconstruction is not promised.

`agent-lab issue --help` documents reproducible Lab defect reporting. Reports use
stable keys, suppress identical duplicates and attach new evidence to open Issues.
Only repeated default runner-validation failures are automatically published.
Pending reports survive encrypted recovery and are retried from keepalive. Raw logs
are never automatically published. This repository is public: manually summarize
sensitive evidence instead of relying solely on redaction.
