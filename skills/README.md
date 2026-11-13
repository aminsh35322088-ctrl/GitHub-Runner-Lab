# Agent skills

This directory contains project-local agent skills that are safe to vendor and read from the repository.

## Sigillo

- Skill: [`skills/sigillo/SKILL.md`](sigillo/SKILL.md)
- Upstream: https://github.com/remorses/sigillo/tree/main/skills/sigillo
- Integration notes: [`docs/sigillo.md`](../docs/sigillo.md)

The Sigillo skill is vendored from the upstream open-source project and extended with Runner Lab-specific bootstrap and credential-boundary notes. Refresh it from upstream when the Sigillo CLI or official skill changes.

To install the upstream skill globally into supported coding agents:

```bash
npx -y skills add remorses/sigillo -g -y
```
