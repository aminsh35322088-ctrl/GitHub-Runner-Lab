# Runner Credential Vault (Cloudflare Worker)

A small, read-only credential broker for the GitHub Runner Lab. Credential values remain in Cloudflare Worker Secrets; the Runner asks for one named value when it needs it. Changing a credential in Cloudflare deploys a Worker version, but does not restart the GitHub Runner.

## Why it does not use a Cloudflare API token

A Worker can read its own bound secrets through `env`. The Cloudflare API does not return existing Worker Secret values, and an API token that can write Worker secrets would create a powerful control-plane credential inside the Worker. This implementation therefore has no Cloudflare API token and no secret-write endpoint. Add, rotate, and delete credential secrets directly under the dedicated Worker’s **Settings → Variables and Secrets**.

## Bindings / naming

- `BROKER_ACCESS_TOKEN`: separate, random bearer token required for all credential API routes. Do **not** use the `CRED_` prefix for this binding.
- `CRED_GITHUB_TOKEN`, `CRED_TAILSCALE_KEY`, etc.: credential values that the Runner is allowed to retrieve.
- Any other Worker binding or secret without the `CRED_` prefix is never returned by the credential API.

Credential suffixes must match `^[A-Z][A-Z0-9_]{0,79}$`. Values must be non-empty strings.

## API

- `GET /health`: public liveness check; returns no binding names or values.
- `GET /v1/credentials`: requires `Authorization: Bearer <BROKER_ACCESS_TOKEN>`; returns only configured `CRED_*` names.
- `GET /v1/credentials/GITHUB_TOKEN`: returns only `CRED_GITHUB_TOKEN` as JSON. The prefix is omitted in the URL.

All responses use `Cache-Control: no-store`. There is no mutation route, bulk download route, CORS permission, query-string auth, or logging of request/credential contents.

## Deploy

From this directory, with a Cloudflare account authorized to deploy Workers:

```bash
npx wrangler@latest deploy
```

Create the reserved `BROKER_ACCESS_TOKEN` using Cloudflare’s secret UI or Wrangler. Generate a random token of at least 32 characters. Set it separately as the repository Actions secret `CREDENTIAL_VAULT_TOKEN` and set the Worker base URL as repository Actions variable `CREDENTIAL_VAULT_URL`.

For every credential that the Runner should be able to read, add a Worker Secret whose name starts with `CRED_`, for example `CRED_GITHUB_TOKEN`. Never name the broker-auth token or a control-plane token with this prefix unless intentionally exposing that credential to the Runner.

## Runner client

```bash
agent-lab credentials list
agent-lab credentials get GITHUB_TOKEN
agent-lab credentials exec GITHUB_TOKEN GH_TOKEN -- gh api user
```

Prefer `exec` over `get` in interactive sessions. `get` prints the requested value to stdout by design. `exec` retrieves the value without printing it, exports it only to the child process as the selected variable, and then replaces the client process with the requested command.

The client reads a short-lived config file written at runner startup to `/dev/shm/agent-credential-vault.json`. The file is mode 0600 and disappears when the ephemeral host is discarded. The repository workflow deliberately continues if the vault is not configured, so a vault outage does not block RDC startup.

## Security model / limits

This is a broker, not a sandbox. Anyone who has `CREDENTIAL_VAULT_TOKEN` can retrieve every `CRED_*` value. Since the Lab Agent and the Runner share the same host identity, code running with that identity can use the broker if it can read the runtime config. Do not put credentials here that the Agent is not intended to use. A credential update is visible on the next broker request; the Runner process itself is not restarted. The Cloudflare Worker may deploy a new version when its secrets change.

Credential names are returned, but not values, by `/v1/credentials`. The Worker has no Cloudflare API token, no admin API, and no endpoint that writes Cloudflare secrets. Manage secrets through the Cloudflare dashboard. For stricter separation, keep sensitive high-privilege tokens in an operation broker that performs a fixed action without returning the raw token.
