import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rdc-lab.yml"
README = ROOT / "README.md"


class VaultOidcSmokeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()
        cls.readme = README.read_text()

    def test_smoke_test_is_an_existing_workflow_dispatch_option(self):
        self.assertIn("- 'Vault smoke test'", self.workflow)
        self.assertNotIn("workflow_run:", self.workflow)

    def test_smoke_test_uses_a_separate_short_lived_oidc_job(self):
        self.assertIn("vault-smoke-test:", self.workflow)
        self.assertIn("timeout-minutes: 5", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("inputs.action == 'Vault smoke test'", self.workflow)
        self.assertIn("inputs.action == 'Start / Resume'", self.workflow)

    def test_smoke_test_fetches_pinned_private_client_and_lists_metadata_only(self):
        self.assertIn("repos/aminsh35322088-ctrl/GitHub-runner-vault/contents/client/vault.mjs", self.workflow)
        self.assertIn("VAULT_CLIENT_REF: 5712a78c7c6cff1da4dd662e506c73d459eb2527", self.workflow)
        self.assertIn('node "$RUNNER_TEMP/vault.mjs" list', self.workflow)
        self.assertIn("Secret values returned: **never**", self.workflow)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", self.workflow)

    def test_smoke_test_does_not_boot_the_persistent_runner_job(self):
        self.assertIn("if: " + "$" + "{{ github.event_name != 'workflow_dispatch' || inputs.action == 'Start / Resume' }}", self.workflow)
        self.assertIn("Vault smoke test", self.readme)
        self.assertIn("without starting the long-lived RDC/Tailscale runner", self.readme)


if __name__ == "__main__":
    unittest.main()
