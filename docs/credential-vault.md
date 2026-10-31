# Runner Credential Vault integration

This document describes how the Lab relates to the **Runner Credential Vault**, a separate
private repository (`aminsh35322088-ctrl/GitHub-runner-vault`) that deploys a Cloudflare Worker.
The vault lets an agent on the runner **use** a non-GitHub / non-Tailscale credential (for example a
Cloudflare API token or a Railway token) **without ever seeing it**, and lets the agent **ask the owner**
to register a new credential through a one-time link.

The vault is not part of the Lab and is not required for the Lab to work. The existing
`AGENT_GITHUB_TOKEN`, Tailscale OAuth and RDC mechanisms are unchanged.

## Why a separate Worker and not something on the runner

Agents on the runner run as a user with passwordless `sudo` and Docker access, and the Actions runner
processes use the same account. Nothing stored on the runner (files, tmpfs, an on-runner broker) is
therefore a real boundary against a compromised or prompt-injected agent. The secret must live elsewhere.
In the vault design:

- the stored token never leaves the Worker (`proxy` mode): the agent says *"call this URL with credential X"*,
  the Worker adds the token, makes the call, and returns the response with any echoed secret redacted;
- the Worker only calls hosts the **owner** approved for that credential;
- the Worker's code lives in a different repository, so an agent with write access to the Lab cannot change it.

## What this repository changes

Exactly one thing: the `runner-lab` job in `.github/workflows/rdc-lab.yml` gets

```yaml
permissions:
  contents: write     # unchanged (repeated, because a job-level block replaces the workflow-level one)
  actions: write      # unchanged (repeated for the same reason)
  id-token: write     # new
```

`id-token: write` only allows the job to **request a GitHub OIDC token**. It does not grant access to the
repository, to secrets, or to any other service. A token is useful only to a service that has been configured to
trust it. The vault trusts it only when **all** of these hold:

| Claim | Required value |
| --- | --- |
| `iss` | `https://token.actions.githubusercontent.com` |
| `aud` | the vault's own origin |
| `repository` / `repository_id` | this repository (`RUNNER_REPOSITORY`, `RUNNER_REPOSITORY_ID`) |
| `ref` | `refs/heads/main` by default (`RUNNER_ALLOWED_REFS`) |
| `event_name` | `workflow_dispatch` or `schedule` by default (`RUNNER_ALLOWED_EVENTS`) |

Pull-request and fork runs are therefore rejected. The `account-switch` and `relaunch` jobs keep the
workflow-level permissions and do not get `id-token`.

> Merging this change touches `.github/workflows/rdc-lab.yml`, which is in the workflow's own `push` path filter,
> so it starts a new Lab run on `main` (the normal self-handover applies).

## Honest limits

- OIDC authenticates the **workflow run**, not an individual process. Any process that can read the job's
  `ACTIONS_ID_TOKEN_REQUEST_URL` / `ACTIONS_ID_TOKEN_REQUEST_TOKEN` variables can obtain a token. The vault
  therefore also limits what a session can do: 30-minute sessions, owner-approved hosts only, header allowlist,
  no redirects, size and rate limits, an append-only audit log, and an owner-side "revoke all sessions" button.
- Whether those variables are visible inside an RDC agent session depends on how the RDC process is started
  within the job step. This must be confirmed with a live test after deployment (see below). If they are not
  inherited, the client needs a small wrapper that exports them when the session starts; that would be a
  separate, reviewed change.
- `inject` delivery (returning a token value to the runner) is **not implemented** in the vault.

## Using it from the runner

The runner client is a single zero-dependency file, `client/vault.mjs`, in the vault repository
(see `docs/runner-usage.md` there for all commands).

```bash
export VAULT_URL=https://<vault-origin>
node vault.mjs list                                     # names and metadata only
node vault.mjs use CF_API_TOKEN GET \
  https://api.cloudflare.com/client/v4/user/tokens/verify --purpose "verify token"
node vault.mjs request --name RAILWAY_TOKEN --service Railway \
  --purpose "deploy" --doc doc.json                      # prints a one-time owner link
```

Flow for a new credential:

1. The agent finds the name is not in `list` and runs `request` with a setup document
   (what the token is for, where to create it, required and unnecessary permissions, expiry advice,
   official links, and whether it checked the official documentation).
2. The command prints a **one-time link** (valid about 15 minutes, works once, and still requires the owner to
   be signed in with GitHub). The agent gives that link to the owner.
3. The owner opens the page, reads the agent's notes, **chooses the allowed hosts, auth header, lifetime
   (permanent or temporary up to 24 h) and visibility**, pastes the token, and saves.
4. The agent now sees the credential in `list` and can `use` it. It never receives the token.

A credential that is missing, hidden, disabled or expired all answer the same `unavailable`.

## Verification checklist (after the owner deploys the vault)

1. Owner signs in at the vault URL with the owner GitHub account (any other account is refused).
2. Run the Lab; from an agent session run `node vault.mjs list`. Expect a session to be issued and an empty or
   metadata-only list. If it fails with `missing_token`, the OIDC variables are not visible to the agent session.
3. Register a short-lived dummy token for a harmless host, call `use`, and confirm the call appears in the vault's
   audit page with the observed result and no secret value.
4. From a pull-request run, confirm the vault refuses a session.
5. Press "Revoke all runner sessions" and confirm the next call needs a fresh session.

## Rollback

Remove the three added lines (and the repeated two, if desired) from the `runner-lab` job. Nothing else in the Lab
depends on them. The vault can be disabled independently by deleting the Worker.
