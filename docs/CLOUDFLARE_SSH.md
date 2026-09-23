# Cloudflare SSH access

Runner Lab can expose its SSH daemon through an outbound-only Cloudflare Tunnel. No public port, TUN device, or Tailscale networking is required.

## Required repository configuration

Configure these values before expecting Cloudflare SSH to become the primary access path:

- Secret `CLOUDFLARE_TUNNEL_TOKEN`: token for a remotely-managed Cloudflare Tunnel.
- Secret `OPENCODE_BOT_SSH_PUBLIC_KEY`: public key returned by the Telegram bot's `ssh(action="key.public")`.
- Variable `CLOUDFLARE_SSH_HOSTNAME`: the Access hostname routed to this runner, for example `runner.example.com`.

On Cloudflare, configure the tunnel's published application as SSH to `localhost:22`. Protect the hostname with a Cloudflare Access self-hosted application and a Service Auth policy accepted by the bot's Service Token.

## Runtime behavior

The existing Runner Lab workflow starts `scripts/cloudflare-ssh.sh` before access-mode selection.

When all Cloudflare inputs are present:

1. A pinned/checksummed cloudflared client is installed.
2. OpenSSH server is configured for public-key-only authentication.
3. The bot public key is installed for the GitHub runner account.
4. The tunnel starts using the `TUNNEL_TOKEN` environment variable, keeping the tunnel token out of process arguments.
5. Runner Lab selects Cloudflare SSH as its primary access path and skips Remote Desktop Commander.
6. The existing keepalive loop health-checks and restarts Cloudflare SSH if required.

If Cloudflare configuration is absent, the current RDC path remains available as a migration fallback. This avoids losing runner access before the first Cloudflare end-to-end test.

## After validation

Once the Cloudflare path has been proven end-to-end, the remaining RDC bootstrap/state fallback can be removed in a separate cleanup without changing the bot-side SSH API.
