from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rdc-lab.yml"


class RdcTailscaleIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.runner = self.text.split("  runner-lab:", 1)[1].split("  relaunch:", 1)[0]

    def test_tailscale_action_is_pinned_and_uses_expected_oauth_secrets(self):
        self.assertIn(
            "uses: tailscale/github-action@d1b6cd204f8dceda5b3eaad7f1f767be390056cd # v4",
            self.runner,
        )
        self.assertIn("secrets.TS_OAUTH_CLIENT_ID", self.runner)
        self.assertIn("secrets.TS_OAUTH_SECRET", self.runner)
        self.assertIn("Validate Tailscale OAuth secrets", self.runner)

    def test_runner_uses_stable_hostname_and_early_logout(self):
        self.assertIn("TAILSCALE_HOSTNAME: GitHub-Lab", self.text)
        self.assertNotIn("GitHub-Lab-${{ github.run_id }}", self.text)
        cleanup = self.runner.index("- name: Disconnect Tailscale before handoff")
        handoff = self.runner.index("- name: Queue Successor Run")
        self.assertLess(cleanup, handoff)
        self.assertIn("./scripts/stop-tailscale.sh", self.runner[cleanup:handoff])

    def test_root_ssh_is_normalized_to_runner_before_ssh_is_enabled(self):
        connect = self.runner.index("- name: Connect Tailscale")
        normalize = self.runner.index("- name: Normalize root SSH sessions to runner")
        enable = self.runner.index("- name: Enable and verify Tailscale SSH")
        self.assertLess(connect, normalize)
        self.assertLess(normalize, enable)
        self.assertIn("bash ./scripts/configure-ssh-user-alias.sh", self.runner[normalize:enable])

    def test_runner_joins_tailnet_without_becoming_exit_node(self):
        self.assertIn("tags: tag:ssh", self.runner)
        self.assertIn("hostname: ${{ env.TAILSCALE_HOSTNAME }}", self.runner)
        self.assertIn("sudo tailscale set --ssh", self.runner)
        self.assertIn("TAILSCALE_HEALTH=READY", self.runner)
        self.assertNotIn("--advertise-exit-node", self.runner)


if __name__ == "__main__":
    unittest.main()
