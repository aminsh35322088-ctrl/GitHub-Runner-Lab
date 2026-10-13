# Lab durability and incident reporting implementation plan

Goal: complete the approved persistence/reproducibility and model onboarding work,
with deduplicated GitHub incident reporting and verified recovery.

Architecture: extend the existing Python/Bash toolkit and existing workflow. Keep
project installation policy in the project's runner hook. Checkpoints remain
encrypted; caches remain disposable accelerators, never the source of truth.

Constraints: no new workflows; latest stable tools remain enabled; GitHub writes
through the connector; execute validation on RDC; preserve unrelated work.

- [ ] Add regression tests for readiness recovery/project gates and lifecycle boundaries.
- [ ] Record actual versions, Lab SHA, platform and project lockfile hashes; expose
      a machine-readable onboarding command and reproduction manifest comparison.
- [ ] Add incident CLI with explicit category/reproduction, sanitization, exact
      fingerprint deduplication, local locking, bounded output and API failure handling.
      Persist pending reports in authenticated checkpoints; retry without blind POST retries.
- [ ] Connect repeated runner validation failures to incident reporting; do not
      classify project failures as Lab incidents or publish raw logs automatically.
- [ ] Fix cache key runtime identity; include run attempt; preserve existing cache fallback.
- [ ] Double-check source, run regression suite, syntax/lint, quick/full live validation,
      failure injection and independent encrypted recovery probe; publish through GitHub.

Review focus: secrets in reports; network failure mistaken for no duplicates;
ambiguous POST timeouts; cache loss after rotation; recovery failure falsely READY.

## Verification checkpoint

- Implemented environment evidence, onboarding CLI, incident reporting, recovery
  readiness gates, runtime cache keys and encrypted pending-report recovery.
- Existing Ubuntu CI run 38401818448 passed regression, shell syntax and Python compile.
- Local full suite had one baseline-only process visibility failure, reproduced on
  unchanged main; mounted /proc did not expose the child PID. Ubuntu CI passed it.
- Live RDC commands timed out three times, including pwd. Live quick/full acceptance,
  actual rotation/cache restore and reporter submission from the runner remain pending.
- Follow-up regression excludes advisory degradation from automatic Issue creation.

References: GitHub dependency caching reference (immutable keys, cache eviction),
and REST Issues API documentation; no new workflow added.
