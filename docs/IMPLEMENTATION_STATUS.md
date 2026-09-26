# Engineered Agent Lab implementation report

The feature branch is ready for review and a controlled first deployment.

## Delivered

- Safe, fast-forward-only workspace preparation that preserves dirty, divergent, and unpublished work.
- Recoverable clone/worktree checkpoints with Git bundles, staged and unstaged patches, filtered untracked source, exact-file checksums, authenticated encryption, safe extraction, and recovery into a new directory.
- Latest-only encrypted persistence on the dedicated `agent-checkpoints` branch plus short-retention workflow artifacts.
- Bounded managed jobs with clean environments, ephemeral argv, durable reports, output caps, timeout/cancellation, descendant cleanup, process-group RSS, per-workspace exclusion, drain, and constrained Docker execution.
- Optional command-scoped fine-grained GitHub PAT support without propagating the token into jobs, checkpoints, caches, repository configuration, or URLs.
- Pinned RDC 0.2.51 supervisor health based on actual channel reachability and heartbeat freshness, automatic restart after consecutive failures, and stricter health exit codes.
- Watchdog retry/JSON fixes, crash-loop and kill-switch handling, verified-ready handoff metrics, explicit cache restore/save/pruning, immutable Action SHAs, and final token cleanup.
- Loopback fault fixtures, readiness reporting, updated operator documentation, and regression coverage.

## Verification

Validation ran through Remote Desktop Commander on the actual Ubuntu runner:

- 19 regression tests passed at the time of this review. The suite has since grown and now runs on every push and pull request through the `Regression Tests` workflow.
- `bash -n`, ShellCheck, Python byte-compilation, Node syntax checking, Actionlint, and `git diff --check` passed.
- A real constrained Alpine managed job passed with no Docker socket, non-root execution, no network, and CPU/memory limits.
- The installed RDC package is 0.2.51, and its runtime exposes the reachability and heartbeat fields used by the supervisor.

The branch does not alter the currently running RDC process. After merge, use one controlled manual dispatch to validate the new supervisor, encrypted checkpoint branch write/restore, and optional PAT permissions with the repository secrets. Existing target-repository policy still controls which project-specific full suites and external-service checks may run.
