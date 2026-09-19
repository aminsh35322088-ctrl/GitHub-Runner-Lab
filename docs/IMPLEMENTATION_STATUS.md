# Agent Lab implementation checkpoint

Work was paused at the user's request on 2026-09-19. This branch is a work-in-progress snapshot, not a production-ready release. Do not merge or deploy it until the remaining integration work and tests pass.

## Changes drafted

- Fast-forward-only workspace preparation preserving dirty and unpublished work.
- Git bundle checkpoints covering clones/worktrees, staged and unstaged patches, filtered untracked source files, checksums and recovery into a new directory.
- Authenticated encrypted checkpoint packaging and a draft dedicated-branch persistence command.
- Detached managed jobs with reports, timeout/cancellation, resource measurements, per-workspace exclusion, optional Docker limits, and drain support.
- Command-scoped GitHub PAT helper, cache cleanup and readiness reporting.
- A pinned-RDC adapter design reporting actual channel heartbeat freshness, plus stricter health checks.
- Watchdog retry output/error handling fixes and draft lifecycle integration.
- Loopback-only fault fixtures for delays, errors and interrupted streaming.

## Required before merge/deployment

1. Complete workflow wiring. In particular, install/pin RDC 0.2.51 and the adapter, provision the optional PAT securely, invoke recovery and final durable checkpoints, and update cache handling.
2. Add regression/integration tests for all reproduced failures and all newly added commands. Only Python parsing and shell syntax have been checked so far.
3. Complete and review job supervision, signal/descendant cleanup, log/report retention, disk limits, secret handling and recovery semantics.
4. Validate dedicated checkpoint-branch synchronization, archive bounds, errors/retries and retention. Git history currently retains previous encrypted snapshots.
5. Update the bot-owned project runner separately: this branch refuses the legacy /app/node_modules hook. No bot repository changes have been made.
6. Correct README reliability statistics and update README/AGENTS command documentation.
7. Implement and validate the bot-specific test scenarios against the fixtures. Fixtures alone do not validate bot behavior.
8. Perform real heartbeat, handoff, recovery, parallel workspace and resource-limit tests. PAT and real Telegram checks require the corresponding secrets.

No workflow was created, no main branch was changed and the live RDC process was not restarted by this work. Existing bot workspace edits were not modified.
