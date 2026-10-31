# Deploy the private Runner Credential Vault

The Deploy button in the GitHub Runner Lab README launches a manually approved GitHub Actions deployment. It does **not** send the Vault source to Cloudflare as a public repository.

## Source and access model

- The deployment workflow lives in the public `GitHub-Runner-Lab` repository.
- It checks out `aminsh35322088-ctrl/GitHub-runner-vault` with a read-only-capable GitHub token.
- Build, tests, D1 migrations and Wrangler deployment run from that private checkout.
- The Vault source, migration files and deployment implementation remain private.
- Only users with permission to run Actions in the Lab repository and access to its configured secrets can use this button. It is not a public one-click installer for arbitrary visitors because Cloudflare's native Deploy button requires a public source repository.

The workflow is manual-only (`workflow_dispatch`), runs only when dispatched from the Lab's `main` branch, and always checks out the private Vault's reviewed `main` branch. It is not run on push, pull requests or schedule. This prevents a caller from selecting an arbitrary unreviewed Vault ref while deployment secrets are present.

## One-time GitHub Actions secrets

Open **GitHub Runner Lab → Settings → Secrets and variables → Actions → New repository secret** and configure:

| Secret | Purpose |
| --- | --- |
| `AGENT_GITHUB_TOKEN` | Existing token with read access to the private Vault repository. |
| `CLOUDFLARE_API_TOKEN` | Cloudflare account API token used only by the deployment job. |
| `CLOUDFLARE_ACCOUNT_ID` | 32-character Cloudflare account ID. |
| `VAULT_BETTER_AUTH_SECRET` | Persistent Better Auth signing secret; at least 32 characters. |
| `VAULT_ENCRYPTION_KEY` | Persistent base64-encoded 32-byte AES encryption key. |

Generate two different values; never reuse one value for both keys:

```bash
openssl rand -base64 32
openssl rand -base64 32
```

Use the first output for `VAULT_BETTER_AUTH_SECRET` and the second for `VAULT_ENCRYPTION_KEY`. Store them directly as GitHub Actions secrets. Do not paste them into an Issue, chat, workflow input or source file.

**Do not rotate either Vault key on a routine redeploy.** Changing `VAULT_ENCRYPTION_KEY` makes credentials already encrypted in D1 unreadable. Changing `VAULT_BETTER_AUTH_SECRET` invalidates existing auth sessions.

### Cloudflare token permissions

Create an account API token scoped to the Cloudflare account that will host the new Worker. The deploy job must be allowed to:

- create a new Worker (**Workers product Admin** for first creation); future updates of an existing Worker can use **Editor** at the Worker or product scope;
- list D1 databases (**D1 Read**) and create the database / apply SQL migrations (**D1 Write**);
- read the relevant account metadata needed by Wrangler.

The token is only injected into the isolated deployment job. It is not placed in the Lab's long-running runner environment or made available to agents. Cloudflare documents the role model in [Workers authorization](https://developers.cloudflare.com/workers/authorization/), the [D1 create permission](https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/create/), and the Wrangler CI authentication flow in [GitHub Actions deployment](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/).

## Merge order and run deployment

Merge [GitHub-runner-vault PR #5](https://github.com/aminsh35322088-ctrl/GitHub-runner-vault/pull/5) first. That PR adds the non-interactive deploy entrypoint consumed by this workflow. The job checks for that capability and fails with an actionable message if Vault `main` has not been updated yet.

Then open [Actions → Deploy private Runner Credential Vault](https://github.com/aminsh35322088-ctrl/GitHub-Runner-Lab/actions/workflows/deploy-runner-vault.yml), choose **Run workflow**, and review the inputs.

Defaults are intentionally safe for the existing installation:

| Input | Default |
| --- | --- |
| New Worker name | `runner-vault-v2` |
| Owner | `aminsh35322088-ctrl` |
| Trusted runner repository | `aminsh35322088-ctrl/GitHub-Runner-Lab` |
| Trusted branch | `main` |
| Trusted workflow | `.github/workflows/rdc-lab.yml` |
| Allowed events | `workflow_dispatch,schedule,push` |

The default Worker name deliberately avoids overwriting the existing `runner-vault` Worker or its encryption keys and D1 data. The workflow also refuses the exact name `runner-vault` to prevent accidental replacement. Keep the same Worker name and the same two persistent key secrets for future updates. The deployment script searches for an existing D1 database with the matching name and reuses it; it does not create another database on each redeploy.

The workflow runs `pnpm validate` (typecheck, all tests and production build) before it deploys.

## Finish configuration after first deploy

1. Read the Worker URL printed by Wrangler in the workflow log.
2. Create a GitHub OAuth App. Set the callback URL to `https://<worker-origin>/api/auth/callback/github`.
3. In the Cloudflare Worker settings, add these as encrypted secrets:
   - `GITHUB_CLIENT_ID`
   - `GITHUB_CLIENT_SECRET`
4. Set `RUNNER_VAULT_URL` in the trusted runner repository's **Actions variables** to the exact HTTPS Worker origin (no path or trailing slash).
5. Run a normal main-branch Lab workflow, then verify from a real RDC session with `vault list`.

The workflow summary repeats the post-deployment checklist. Do not put OAuth credentials in workflow inputs or commit them to source.

## Existing Worker safety

The workflow does not edit the existing `runner-vault` deployment by default. Do not change the input to `runner-vault` unless you have the original auth/encryption key material and have confirmed that its D1 database binding is the intended production database. A deploy with unknown keys can make the existing encrypted credentials unrecoverable.
