# Runner Credential Vault integration

This document describes the optional Runner Credential Vault connection used by
GitHub Runner Lab. The Lab discovers the deployed Worker from `deployment.json`
in the Vault repository; there is no Lab-side Vault URL or client-revision secret
to maintain. GitHub access (`gh`, HTTPS Git and GHCR) remains a separate Agent
capability and does not depend on Vault availability.

## Deploy and enroll the Vault

Deploy the Vault through its setup wizard — the supported build method:

https://aminsh35322088-ctrl.github.io/GitHub-runner-vault/

The wizard creates the Worker resources and deploys the Vault. Its deployment
metadata publishes the Worker HTTPS origin and immutable client commit in
`deployment.json`. On a later Lab run, the trusted discovery step validates that
metadata and fetches the client at that exact commit. The existing
`AGENT_GITHUB_TOKEN` is only the Lab's normal GitHub bootstrap credential and a
scoped fallback for private client downloads or anonymous API rate limits; it is
not passed to Vault as a credential.

The Vault owner completes browser sign-in and configures the runner repository
policy in the Vault UI. The Worker pins the verified personal GitHub owner ID and
repository identity. Its OIDC verifier accepts the supported GitHub subject
formats, binds the immutable owner/repository IDs to the separately signed
claims, and checks issuer, audience, expiry, ref, event and workflow policy.
Pull-request/fork contexts are not allowed to enroll or access the Runner API.

## Runtime trust boundaries

The long-lived Lab workflow runs with three distinct Unix identities:

| Identity | Responsibility | Access |
| --- | --- | --- |
| `runner` | Trusted workflow steps, checkout, Tailscale and encrypted state sync | Runner secrets and checkout; never used for RDC |
| `rdc-agent` | Remote Desktop Commander and Agent processes | Dedicated Agent home, sanitized source snapshot, explicit workspace/cache roots, restricted Broker socket |
| `runner-vault-broker` | OIDC exchange and Vault API proxy | Broker code, its own process memory and the restricted socket; no Agent home or shell |

`setup-runner-identities.sh` creates the Agent and Broker identities, assigns a
unique 65,536-ID subordinate UID/GID range for rootless Podman, and removes
privileged group membership. The Agent has no sudo or rootful Docker access.
RDC is launched only through `run-as-rdc-agent.sh`, which creates an empty
environment and adds an explicit, non-secret allowlist. The Agent reads a
root-owned, read-only copy of the Lab source at `/var/lib/rdc-agent/lab`; the
copy excludes `.git` and is separate from the runner's credential-bearing
checkout. Node/npm also run from a private read-only runtime in the Agent home.
Only designated workspaces and caches are shared using explicit ACLs.

The OIDC requester capability is used only by the trusted workflow's Broker
bootstrap. `start-vault-broker.sh` passes the bootstrap configuration through an
anonymous pipe to the Broker process; it is not placed in Broker argv, a file or
its environment. The Broker runs as `runner-vault-broker`, verifies the connecting
peer UID with Linux `SO_PEERCRED`, and serves only the configured `rdc-agent` UID
on `/run/runner-vault/broker.sock`. The socket is group-restricted and has mode
`0660`; the containing directory is `0750`.

The Broker retains the OIDC requester and short-lived Runner Session in process
memory only. It never returns either value to the Agent, never exposes a
session-read/export or arbitrary shell operation, and never writes a Session
cache. The Agent cannot inspect the Broker's process memory or environment.
RDC and Agent processes have no `ACTIONS_ID_TOKEN_REQUEST_URL`,
`ACTIONS_ID_TOKEN_REQUEST_TOKEN`, Runner Session, `RDC_STATE_KEY`, or workflow
bootstrap token in their environment. RDC PID, log and health files are private
to `/var/lib/rdc-agent/.run` rather than shared `/tmp` names.

## Broker protocol and CLI contract

The local JSON protocol is versioned, length-bounded and uses one request per
Unix-socket connection. It rejects unknown fields, mismatched request IDs,
wrong peer UIDs, oversized frames and unsupported operations. The server caps
worker threads before spawning them; excess connections are closed without
creating additional threads. Upstream timeouts/responses are bounded and
redirects fail closed. If runtime verification fails, the runner terminates the
Broker process and removes the socket before continuing in degraded mode.

Only the following operations are available:

| Broker operation | API behavior | Agent visibility |
| --- | --- | --- |
| `status` | Establish/refresh a short Runner Session | Connected/protocol/expiry metadata only |
| `tokens.list` | Read `/api/runner/registry` | Active/visible credential metadata; no values |
| `integrations.list` | Read `/api/runner/integrations` | Reviewed adapter metadata only |
| `registrations.create` | Create `/api/runner/registrations` request | One-time owner link and expiry; no credential input |
| `credential.use` | Proxy `/api/runner/use` | Filtered provider response; never the raw credential |

The `credential.use` payload is limited to HTTPS targets, a bounded method set,
reviewed request headers and a maximum request body. The Vault Worker independently
enforces the owner's credential policy, including hostname and method allowlists,
visibility and lifetime. Provider redirects are not followed by the Broker.

The installed zero-dependency CLI speaks only to the local Broker socket:

```bash
vault status
vault list
vault request --name RAILWAY_API_TOKEN --service Railway --doc railway-token-doc.json
vault use RAILWAY_API_TOKEN GET https://api.railway.com/v2/projects --purpose "list projects"
vault serve RAILWAY_API_TOKEN
```

`vault request` submits documentation and asks the owner to finish registration
in the browser; the raw token is never accepted by the Agent. `vault use` makes
one explicitly requested API call. `vault serve` exposes a reviewed provider
adapter only on `127.0.0.1`; CLI placeholder values are intercepted locally and
never sent upstream as credentials. Git smart-HTTP and the Agent's GitHub CLI
credentials remain independent of Vault.

## Failure behavior and verification

The Vault integration is optional. Missing deployment metadata reports
`VAULT_RUNTIME=DISABLED`. Missing OIDC bootstrap, Broker startup/IPC failure,
policy refusal, or failed runtime verification reports `DEGRADED`; these states
disable Agent Vault operations without blocking RDC, Tailscale or GitHub access.
There is no fallback that copies a Vault credential or Session into an Agent
environment, file or shell helper.

The runtime verifier checks:

- runner, Agent and Broker have distinct UIDs, and Agent/Broker have no sudo or
  rootful Docker group membership;
- socket type, owner group, mode and directory permissions;
- Broker PID/UID and absence of OIDC bootstrap names in its environment/argv;
- RDC PID/UID and absence of OIDC requester, persistence key and bootstrap GitHub
  token names in its environment;
- rootless Podman readiness for the Agent;
- successful `vault status` and metadata-only `vault list` over the restricted
  socket, with no secret-shaped registry fields.

The workflow's separately selectable **Vault smoke test** is a short-lived
trusted OIDC job. It tests the Vault's read-only registry and configured provider
proxies without launching the persistent RDC Lab. After the Broker is deployed,
a normal Lab run also checks the actual Agent-to-Broker protocol before reporting
`VAULT_RUNTIME=READY`.

## Rollback

Remove or rename `deployment.json` in the Vault repository, or disable the Vault
Worker, to make discovery skip the integration. The next Lab run reports
`DISABLED`; the runner still starts RDC and Tailscale and retains its separate
GitHub access. If only the Broker is unhealthy, the Lab reports `DEGRADED` and
leaves Agent-side Vault commands unavailable rather than weakening the process
boundary.
