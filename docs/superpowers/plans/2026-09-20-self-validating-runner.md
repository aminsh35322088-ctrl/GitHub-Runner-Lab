# Self-Validating Runner Lab — Implementation Plan

## Scope
Implement the approved self-validating runner architecture on PR #2 without creating a new workflow. Keep project-validation behavior backward compatible.

## Task 1 — Source-of-truth toolset manifest
Create `config/runner-toolset.json` and `scripts/lab_toolset.py`.
Tests first: manifest schema/version, package profile expansion, required commands, Node version, Goss pin/checksum, unsupported architecture handling.
Then refactor `agent-lib.sh` and `agent-bootstrap.sh` to consume the manifest.

## Task 2 — Host acceptance with pinned Goss
Create `scripts/install-goss.sh` and `scripts/lab_runner_validate.py`.
Tests first: checksum metadata, generated Goss spec, hard vs advisory network classification, disk/inode thresholds, report paths.
Use Goss v0.4.10 with per-architecture SHA256 from the signed GitHub release.

## Task 3 — Functional runner smoke suite
Create `scripts/lab_smoke.py`.
Tests first with command stubs for stage selection, cleanup, failure classification, and timeout behavior.
Implement quick core smoke (native, CMake/Ninja, Node, Python, Git) and full additions (Docker, media).

## Task 4 — Unified validation routing
Extend `agent-run.sh validate` with runner/project/full modes while preserving `validate WORKSPACE`.
Add stable Markdown/JSON/JUnit runner summaries.
Keep PR #2 project validation implementation isolated in `lab_validate.py`.

## Task 5 — Prewarm readiness gate and doctor
Refactor `agent-prewarm.sh` so READY requires quick runner validation after bootstrap.
Extend `agent-doctor.sh` to report manifest/toolchain validation state without duplicating tool lists.

## Task 6 — Fault injection and documentation
Add regression tests for invalid manifest, missing commands, stale RDC heartbeat, Goss checksum mismatch metadata, Docker/media smoke failure, and advisory network degradation.
Update README and AGENTS with the new validation contract and commands.

## Task 7 — End-to-end verification
Run via Remote Desktop Commander:
1. unit/regression suite;
2. bash syntax + Python compile + diff check;
3. `agent-run.sh validate runner quick`;
4. `agent-run.sh validate runner full`;
5. fault-injection checks;
6. legacy project validation contract.
Only after all evidence is green, update the PR description/comment with exact results.

## Review focus
- no duplicated toolset truth;
- no secrets in reports/logs;
- bounded resource use and deterministic cleanup;
- external downloads pinned + checksum verified;
- no false-green on retries/flaky diagnostics;
- backward compatibility for existing PR #2 callers;
- RDC startup priority preserved.
