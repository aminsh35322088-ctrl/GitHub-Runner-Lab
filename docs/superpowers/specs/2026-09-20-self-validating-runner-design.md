# Self-Validating Runner Lab — Design

## Goal
Evolve PR #2 from project-validation hardening into a self-validating engineering runner. A runner is READY only when the host, core toolchain, RDC health, and deterministic smoke checks prove the environment is usable. Project validation remains a separate contract and keeps backward compatibility.

## Constraints
- Reuse the existing PR #2 validation/job/checkpoint architecture; do not create a new workflow.
- GitHub remains the durable source of truth; the runner is disposable.
- RDC must become reachable before heavy preparation. Self-validation runs after bootstrap/prewarm, not before connectivity.
- Existing `agent-run.sh validate WORKSPACE` behavior remains supported.
- All external binaries are pinned and checksum-verified. No `latest` installers.
- Network-dependent checks distinguish hard dependencies from advisory/degraded dependencies.
- No destructive stress tests; smoke tests must be bounded and cleanup-safe.

## Architecture
### 1. Toolset manifest
`config/runner-toolset.json` is the source of truth for:
- toolchain version;
- package profiles (core/build/media/full);
- required commands;
- expected Node version;
- disk/inode thresholds;
- Goss version and per-architecture SHA256;
- critical/advisory network endpoints;
- smoke-test groups.

Bootstrap, prewarm readiness, doctor output, and runner validation consume this manifest through `scripts/lab_toolset.py`.

### 2. Host acceptance
`scripts/lab_runner_validate.py` materializes a concrete Goss spec from the manifest and current machine state, installs the pinned Goss binary into the agent cache when needed, and validates:
- Linux/Ubuntu runner context;
- writable HOME/tmp/workspace/cache paths;
- disk and inode headroom;
- required commands;
- critical DNS/HTTPS reachability;
- RDC process/heartbeat when RDC state is present.

Goss is a validation backend, not a second configuration source.

### 3. Functional smoke tests
`scripts/lab_smoke.py` performs bounded real operations rather than presence-only checks:
- native: C/C++ compile + execute, CMake + Ninja generate/build;
- runtime: Node script, Python venv/imports;
- git: init/commit/worktree and Git LFS availability;
- container: Docker client/server, buildx/compose, run/inspect/remove;
- media: FFmpeg synthetic media + probe/transcode, ImageMagick create/resize/identify.

Quick mode runs deterministic core tests. Full mode adds container/media/network-heavy checks.

### 4. Orchestration and reports
`agent-run.sh validate` gains explicit modes while preserving the legacy path form:
- `validate WORKSPACE` -> project validation (compatibility);
- `validate project WORKSPACE`;
- `validate runner [quick|full]`;
- `validate full WORKSPACE` -> runner full validation then project validation.

Runner validation emits Markdown, JSON, and JUnit. Stage failures carry stable categories: HOST, TOOLCHAIN, NETWORK, DOCKER, MEDIA, RDC, PROJECT, CLEANUP.

### 5. Prewarm integration
`agent-prewarm.sh` runs quick runner validation after bootstrap and command readiness. READY is written only after the quick acceptance layer succeeds. Because prewarm already runs in the background after RDC is reachable, this does not delay initial connectivity.

## Failure policy
- Hard gate: manifest validity, core toolchain, disk/inodes, local filesystem, GitHub DNS/HTTPS, native/runtime smoke, RDC health when expected.
- Degraded/advisory: secondary package registries and nonessential external endpoints.
- Full-only hard gate: Docker and media checks when full validation is explicitly requested.
- Every smoke test owns its temp directory/resources and cleans them in `finally` paths.
- A retry can diagnose transient network failure but must never convert a deterministic failure to green silently.

## Compatibility
PR #2 project validation semantics, interruption-safe cleanup, bounded logs, flaky diagnosis, cache propagation, and managed-job grace windows remain intact. Existing callers do not need to change immediately.

## Verification
- TDD regression tests for manifest parsing, routing, pinned Goss metadata, validation classifications, prewarm gating, and report generation.
- Existing unit/regression suite remains green.
- Real RDC runner verification: quick and full self-validation, Docker/native/media/network smoke tests, then project validation contract.
- Fault injection: missing command, stale RDC heartbeat, invalid manifest, network failure, Docker failure, and forced stage failure must classify correctly and never false-green.
