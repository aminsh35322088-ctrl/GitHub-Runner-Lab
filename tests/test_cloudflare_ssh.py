import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CloudflareSshRunnerTests(unittest.TestCase):
    def test_runner_bridge_keeps_tunnel_token_out_of_process_args(self):
        source = (ROOT / "scripts" / "cloudflare-ssh.sh").read_text()
        self.assertIn('export TUNNEL_TOKEN="$CLOUDFLARE_TUNNEL_TOKEN"', source)
        self.assertIn('tunnel --no-autoupdate run', source)
        self.assertNotIn('--token "$CLOUDFLARE_TUNNEL_TOKEN"', source)
        self.assertIn("unset RUNNER_TRACKING_ID", source)

    def test_cloudflared_is_pinned_and_checksum_verified(self):
        source = (ROOT / "scripts" / "cloudflare-ssh.sh").read_text()
        self.assertIn('CLOUDFLARED_VERSION="2026.5.1"', source)
        self.assertIn("3c6a5ba995a258dbe90f98e5fdb2c2620b7be72c3ca761614f6eb52aee252cea", source)
        self.assertIn("sha256sum -c -", source)

    def test_sshd_is_key_only_and_restricted(self):
        source = (ROOT / "scripts" / "cloudflare-ssh.sh").read_text()
        self.assertIn("PasswordAuthentication no", source)
        self.assertIn("KbdInteractiveAuthentication no", source)
        self.assertIn("PermitRootLogin no", source)
        self.assertIn("AllowUsers runner", source)
        self.assertIn("ssh-keygen -l", source)

    def test_existing_workflow_selects_cloudflare_as_primary_with_rdc_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "rdc-lab.yml").read_text()
        self.assertIn("Start Cloudflare SSH access", workflow)
        self.assertIn("secrets.CLOUDFLARE_TUNNEL_TOKEN", workflow)
        self.assertIn("secrets.OPENCODE_BOT_SSH_PUBLIC_KEY", workflow)
        self.assertIn("vars.CLOUDFLARE_SSH_HOSTNAME", workflow)
        self.assertIn('Cloudflare SSH is primary; Remote Desktop Commander is skipped.', workflow)
        self.assertIn("steps.mode.outputs.rdc == 'true'", workflow)

    def test_keepalive_supervises_cloudflare(self):
        source = (ROOT / "scripts" / "keepalive.sh").read_text()
        self.assertIn("CLOUDFLARE_SSH_ENABLED", source)
        self.assertIn("./scripts/cloudflare-ssh.sh health", source)
        self.assertIn("./scripts/cloudflare-ssh.sh start", source)
        self.assertIn("./scripts/cloudflare-ssh.sh stop", source)


if __name__ == "__main__":
    unittest.main()
