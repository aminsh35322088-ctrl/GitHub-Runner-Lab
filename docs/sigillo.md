# Sigillo integration

Sigillo is the Lab's central credential source for trusted agent work. It supplies the Agent's GitHub provider credential and other provider/API credentials to only the process that needs them. GitHub Actions workflow-control operations remain on GitHub's built-in Actions token and do not depend on Sigillo.

Official references:
- [Sigillo](https://sigillo.dev/)
- [Sigillo CLI documentation](https://github.com/remorses/sigillo)
- [Sigillo agent skill](https://github.com/remorses/sigillo/blob/main/skills/sigillo/SKILL.md)
- [GitHub CLI authentication](https://cli.github.com/manual/gh_auth_login)

## 1. Credential layout

| Credential | Location | Purpose |
| --- | --- | --- |
| `SIGILLO_API_TOKEN` | GitHub Actions repository secret | Allows each ephemeral runner to authenticate to Sigillo non-interactively. |
| `GITHUB_API_TOKEN` | Sigillo project `Amin-creditonal`, environment `prod` | Agent GitHub CLI, HTTPS Git and GHCR authentication. |
| Other provider/API credentials | Sigillo project/environment | Credentials for services used by trusted agent tasks. |
| `github.token` | Automatically provided by GitHub Actions | Workflow-control API: list/cancel/dispatch runs, watchdog recovery and repository status publishing. |
| `ACTIONS_WATCHDOG_TOKEN` | Optional GitHub Actions repository secret | Optional override for workflow-control operations when the built-in token's repository-scoped permissions are insufficient. |

The configured Sigillo project ID is `01M6ZRTJGTG7RNMSMVMVRJB3HY`; the Runner Lab currently uses environment `prod`. The workflow pins these values in its top-level environment.

**Do not move GitHub Actions API operations to Sigillo.** The Actions API path stays authenticated by `github.token` or the optional `ACTIONS_WATCHDOG_TOKEN`. `GITHUB_API_TOKEN` is for the Agent's GitHub identity only.

## 2. GitHub Actions configuration

Required:
- Add a Sigillo API bearer token as the Actions secret `SIGILLO_API_TOKEN`.
- Ensure the Sigillo project above contains `GITHUB_API_TOKEN` in the `prod` environment.
- The GitHub PAT should be a classic PAT for `gh auth login --with-token`. Grant only the repository/org scopes required for the work; include `write:packages` when GHCR publishing is required.

Optional:
- Set repository variable `SIGILLO_API_URL` only when using a self-hosted Sigillo instance. It must be an HTTPS origin; otherwise the workflow defaults to `https://sigillo.dev`.

The workflow validates the Sigillo token using `sigillo me` before saving it, then uses `sigillo login --scope /`, restricts `~/.sigillo` and its config file to owner-only permissions, and verifies the saved credential. The config is created on each ephemeral runner. It is never committed, cached, checkpointed, or uploaded as an artifact.

## 3. Agent GitHub bootstrap

After RDC and Tailscale health checks, the workflow runs the equivalent of:

```bash
sigillo run --project 01M6ZRTJGTG7RNMSMVMVRJB3HY --env prod \
  --command 'set -o pipefail; printf "%s" "${GITHUB_API_TOKEN:?GITHUB_API_TOKEN is missing from Sigillo}" | env -u GITHUB_API_TOKEN ./scripts/agent-github.sh install'
```

The bootstrap:
1. Receives `GITHUB_API_TOKEN` from Sigillo only inside the `sigillo run` child.
2. Pipes it over stdin to `gh auth login --with-token`; it is not passed in argv.
3. Removes `GITHUB_API_TOKEN` from the installer environment.
4. Configures the GitHub CLI credential helper for HTTPS Git and the GHCR Docker credential helper.
5. Verifies API identity, a usable Git credential, the required package scope, and the GHCR helper.

Once that succeeds, trusted RDC/Agent shells use ordinary `gh`, `git`, and `docker` commands. The PAT is not added to a persistent environment variable and is not separately stored as a GitHub Actions secret.

GitHub documents `gh auth login --with-token` as reading a classic PAT from standard input. It notes that the minimum scopes for this flow are `repo`, `read:org`, and `gist`; the Lab additionally checks `write:packages` when configuring GHCR access. See the [official gh auth login documentation](https://cli.github.com/manual/gh_auth_login).

## 4. Use Sigillo for other provider credentials

Use project IDs and environment slugs explicitly:

```bash
sigillo me
sigillo projects
sigillo environments --project 01M6ZRTJGTG7RNMSMVMVRJB3HY
sigillo secrets --project 01M6ZRTJGTG7RNMSMVMVRJB3HY --env prod
sigillo run --project 01M6ZRTJGTG7RNMSMVMVRJB3HY --env prod -- your-command
```

For a command that needs one or more credentials, prefer `sigillo run` instead of reading values into the agent context or writing a dotenv file. Keep the operation narrow and run only trusted code on the host.

Never:
- Read `~/.sigillo/` or `.env` files directly.
- Print, serialize, log, screenshot, copy to chat, or commit secret values.
- Put credential values in command-line arguments, Git URLs, shell history, GitHub outputs, issues, checkpoints, or caches.
- Use `sigillo run` output as a raw-secret transport; Sigillo's output redaction also applies to pipes.
- Run untrusted repository code in the trusted RDC shell or a host managed job merely because the command is wrapped in `sigillo run`. Use the isolated untrusted container execution path.

Sigillo redaction is defense in depth, not a hostile-code security boundary. The child receives the real values, and another process running under the same Unix user may be able to use the same Sigillo access.

## 5. Smoke test and troubleshooting

Run **Actions → Remote Desktop Commander Lab → Run workflow → Sigillo smoke test**. This short-lived job validates Sigillo authentication and visibility of the configured project without starting or restarting the long-lived runner.

| Symptom | Likely cause | Safe action |
| --- | --- | --- |
| Sigillo authentication rejected (401) | Revoked/wrong API token or wrong Sigillo API origin | Check `SIGILLO_API_TOKEN` and `SIGILLO_API_URL`; do not print either value. |
| Project not visible | Token is scoped to another project/account | Run `sigillo projects` and confirm the configured project ID and scope. |
| Environment missing | Wrong slug or token scope | Run `sigillo environments --project PROJECT_ID` and use the exact slug. |
| `GITHUB_API_TOKEN` missing during Agent bootstrap | Secret is absent from the Sigillo `prod` environment or has a different name | Add/repair it in Sigillo's UI; do not add a second GitHub PAT Actions secret. |
| GitHub bootstrap fails | PAT type/scope, Git credential-helper, package scope, or GHCR helper issue | Inspect the safe `AGENT_GITHUB_*` status markers; never print token values. |
| Workflow API requests fail | Built-in Actions token lacks the required repository permission | Review the job's `permissions` block or configure the optional `ACTIONS_WATCHDOG_TOKEN`; do not route workflow API through Sigillo. |

The Sigillo smoke test is diagnostic only; successful Sigillo authentication does not by itself prove the later Agent GitHub credential is valid. The main Runner Lab workflow verifies the GitHub identity and Git/GHCR credential paths as part of bootstrap.
