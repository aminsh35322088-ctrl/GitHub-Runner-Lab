# Sigillo integration

This guide describes how Sigillo is integrated into GitHub Runner Lab, how operators configure it, and how agents should use it safely. It complements the vendored [Sigillo agent skill](../skills/sigillo/SKILL.md); the upstream project documentation remains authoritative for current CLI behavior.

- Upstream project: https://github.com/remorses/sigillo
- Official hosted service: https://sigillo.dev/
- Upstream agent skill: https://github.com/remorses/sigillo/blob/main/skills/sigillo/SKILL.md
- Official CLI reference: https://github.com/remorses/sigillo/tree/main/app/src/docs/cli

## 1. What Sigillo does in the Lab

Sigillo is an optional secrets manager exposed through its CLI. It authenticates the runner to a Sigillo instance, lets an agent discover projects/environments and secret names, and can inject selected secret values into child processes with `sigillo run`.

The Lab uses the standard non-interactive Sigillo API-token path. It does not use a browser/device flow during normal runner startup.

### Keep the three credential paths separate

| Credential path | Purpose | Secret exposure model |
| --- | --- | --- |
| `AGENT_GITHUB_TOKEN` bootstrap | GitHub CLI, HTTPS Git and GHCR access. | Bootstrapped into GitHub's credential helpers; independent of Sigillo and Runner Vault. |
| Sigillo | Project/environment secrets for commands that need values in their process environment. | The CLI holds an API bearer token in the current runner user's private config. `sigillo run` gives the selected values to its child process and redacts matching output by default. |
| Runner Credential Vault | Owner-managed provider API access. | Raw credential values stay in the separately deployed Worker; the runner receives only filtered API responses. |

Sigillo is not proxy-only. A process running as the same Unix user can potentially use the CLI and request whatever the API token is allowed to read. Redaction does not make an untrusted process safe and does not stop a malicious program from using the injected credential. Use Runner Credential Vault when the raw credential must never enter the runner.

## 2. GitHub Actions configuration

### Required for the optional integration

| GitHub setting | Type | Required | Description |
| --- | --- | --- | --- |
| `SIGILLO_API_TOKEN` | Actions secret | Optional for the Lab; required to enable Sigillo | Sigillo bearer API token. The value is never printed by the workflow. |
| `SIGILLO_API_URL` | Repository variable | Optional | HTTPS origin of a self-hosted Sigillo instance. Defaults to `https://sigillo.dev`. |

Create the API token in the Sigillo dashboard. Prefer a token scoped to the one project the agents need. If the workflow task needs to list/read multiple environments, restrict the token to only those required environments. Avoid broad organization-wide access when project/environment scope is sufficient.

Add the token only in GitHub **Settings → Secrets and variables → Actions → New repository secret**. Do not put the raw token in a workflow YAML file, repository variable, `.env`, issue, chat, or command argument.

For self-hosted Sigillo, add `SIGILLO_API_URL` as a repository variable. It must be an HTTPS origin only. The workflow rejects a value containing credentials, a path, query string, or fragment before it can be used as the API base URL.

### Startup sequence

The existing `.github/workflows/rdc-lab.yml` step named **Install and authenticate Sigillo CLI (optional)** runs early in the `runner-lab` job, before RDC starts.

1. Read `SIGILLO_API_TOKEN` from GitHub Actions Secrets and expose it to the process as `SIGILLO_TOKEN` (the variable name expected by Sigillo).
2. Normalize/validate `SIGILLO_API_URL`; use `https://sigillo.dev` when the variable is empty.
3. Install the Sigillo CLI with npm.
4. Call `SIGILLO_TOKEN=... sigillo me` as a preflight. This is important because `sigillo login` with a bearer token saves the token but does not prove that the API accepts it.
5. Only after preflight succeeds, run `sigillo login --scope /` to save the token in the runner user's config.
6. Apply permissions (`700` to `~/.sigillo`, `600` to `~/.sigillo/config.json`) and call `sigillo me` again.
7. Mark the step ready only after post-save verification. The token itself, API response body and config content are not emitted to logs or summaries.

The step reports `SIGILLO_CLI_READY=true` only after successful API authentication and persistence. Any known or unexpected bootstrap failure is converted to a warning and Sigillo remains unavailable; RDC/Tailscale startup must continue.

The file `~/.sigillo/config.json` belongs to the current ephemeral runner user. It is not a repository file and must not be added to encrypted workspaces, GitHub caches, or artifacts. A successor generation receives the Actions secret again and performs a fresh non-interactive bootstrap.

## 3. Verify the integration

Run these commands from a trusted RDC shell:

```bash
sigillo --version
sigillo me
sigillo projects
```

Use the project ID from `sigillo projects`, not the display name:

```bash
PROJECT_ID='<project-id-from-sigillo-projects>'

sigillo projects get "$PROJECT_ID"
sigillo environments --project "$PROJECT_ID"
sigillo secrets --project "$PROJECT_ID" --env dev
sigillo secrets --project "$PROJECT_ID" --env preview
sigillo secrets --project "$PROJECT_ID" --env prod
```

`sigillo secrets` lists names and metadata, not the secret values. The CLI accepts explicit `--project` and `--env` overrides, so the Lab does not need to run interactive `sigillo setup` for each workspace.

### Dedicated smoke test

Open **Actions → Remote Desktop Commander Lab → Run workflow → Vault smoke test**. This selects the existing `vault-smoke-test` job and does not start or restart the persistent `runner-lab` job.

The smoke test:

- checks the Actions secret using `sigillo me`;
- lists projects and checks whether the configured `Amin-creditonal` project is visible to the token;
- writes only a small pass/fail status to the step log/summary, never the token or secret values;
- proceeds to the separate Runner Credential Vault OIDC/registry and GitHub/Cloudflare credential checks.

A successful Sigillo step does not imply that the later Vault provider checks passed; review each step independently.

## 4. Use Sigillo from an agent

### List project/environment metadata

```bash
sigillo me
sigillo projects
sigillo environments --project <PROJECT_ID>
sigillo secrets --project <PROJECT_ID> --env dev
```

The CLI uses project IDs, not project names, for `--project`. A project-scoped API token may still be restricted to a subset of that project's environments; a successful `sigillo me` does not guarantee access to every environment.

### Run a trusted command with secret injection

```bash
sigillo run --project <PROJECT_ID> --env dev -- npm test
```

Use `--command` only for shell operations such as `&&`, pipes, redirects or variable expansion. Quote the command to keep expansion inside the child shell:

```bash
sigillo run --project <PROJECT_ID> --env preview --command 'node deploy.js --token "$DEPLOY_TOKEN"'
```

### Send a single value directly to a tool

Do not print a value for debugging or move it through model output. Keep it inside a direct pipe:

```bash
sigillo secrets get DEPLOY_TOKEN --project <PROJECT_ID> --env dev --raw | deploy-tool --token-stdin
```

The exact command and flags should be confirmed with the installed CLI help when working against a different Sigillo release:

```bash
sigillo --help
sigillo secrets get --help
sigillo run --help
```

### Important redaction limit

Sigillo redacts matching secret values from `sigillo run` stdout/stderr by default, including when the output is piped. A downstream command receiving the output of `sigillo run` may therefore receive literal asterisks instead of the secret. Do not use `sigillo run` output as a raw-secret transport.

Redaction is defense in depth, not a guarantee that every possible secret is removed. Short or low-entropy values may not match the redaction heuristic. Avoid commands that print credentials, do not disable redaction for inspection, and do not run untrusted code on the trusted host just because it is wrapped with `sigillo run`.

For files, prefer `sigillo run` environment injection. The optional `--mount` mode writes a temporary owner-only file and deletes it after execution, but it still puts secret material on disk while the child is running; use it only when the consuming application requires a file.

## 5. Agent safety rules

- Never read `.env` files or any file under `~/.sigillo/` directly. Use `sigillo me` to inspect authentication and setup status.
- Never echo, cat, serialize, log, screenshot, paste or commit token/config/secret values.
- Never pass a Sigillo token as a CLI argument. Use the runner's Actions-secret bootstrap or a protected `SIGILLO_TOKEN` environment variable in a controlled CI environment.
- Never add `~/.sigillo` to checkpoints, caches, artifacts, workspace archives or repository files.
- Never use `--disable-redaction` to obtain a secret for the agent context.
- Do not treat host-side Sigillo as a sandbox. Trusted agent processes share the runner Unix identity.
- Managed jobs use a clean isolated HOME; do not assume they inherit the interactive runner's Sigillo login. Do not pass the bearer token through `--pass-env` as a workaround.
- Do not create, rotate, broaden, revoke or delete tokens/secrets unless the user explicitly requests it. Keep scope as narrow as the task allows.
- Prefer Runner Credential Vault for credentials whose raw value must remain off the runner.

## 6. Troubleshooting

| Symptom | Likely cause | Safe action |
| --- | --- | --- |
| `error: not logged in` on the current runner | Bootstrap did not create a local config, the step was skipped, or the agent is in a managed job with a clean HOME. | Check the **Install and authenticate Sigillo CLI (optional)** step and its summary. On the Lab runner, verify `SIGILLO_API_TOKEN` exists in Actions Secrets; do not start browser login. |
| HTTP `401 unauthorized` | Token is revoked/incorrect, or it belongs to a different Sigillo API instance. | Confirm the Actions secret was replaced with the current bearer token and that `SIGILLO_API_URL` points to the instance that created it. |
| HTTP `403` | Token authenticated but the requested project/environment or operation is outside its access scope. | Check project ID and allowed environment scope. Do not broaden the token without owner approval. |
| Project list is empty or expected project is missing | Token belongs to another project/org, has limited visibility, or the project ID/name was confused. | Run `sigillo projects`, use the returned project ID, and confirm the intended account/instance. |
| Environment not configured/found | Display name was passed instead of project ID, env slug is wrong, or the project has no access to that slug. | Run `sigillo environments --project PROJECT_ID` and use the exact slug. |
| Saved config is rejected after login | The supplied token did not pass the post-save API check or the API URL changed. | Check the workflow summary; update the GitHub Actions secret/variable and rerun Start / Resume. Never edit the config file by hand. |

A successful `sigillo login` command alone is not an authentication test; it can save a bearer string without the server accepting it. The Lab's preflight and post-login `sigillo me` checks are intentional.

## 7. Vendored agent skill and upstream references

The full upstream Sigillo skill is vendored at [`skills/sigillo/SKILL.md`](../skills/sigillo/SKILL.md), with Lab-specific integration notes appended. It contains the upstream guidance for project bootstrap, secret-safe pipelines, package scripts, worktree scoping, CI, and self-hosted instances.

Install the official upstream skill globally when desired:

```bash
npx -y skills add remorses/sigillo -g -y
```

Official references:

- [Sigillo README](https://github.com/remorses/sigillo)
- [Sigillo agent skill](https://github.com/remorses/sigillo/blob/main/skills/sigillo/SKILL.md)
- [CLI reference](https://github.com/remorses/sigillo/tree/main/app/src/docs/cli)
- [CLI changelog](https://github.com/remorses/sigillo/blob/main/cli/CHANGELOG.md)
- [Hosted Sigillo](https://sigillo.dev/)
