import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-runner-vault.yml"


class PrivateVaultDeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()
        cls.readme = (ROOT / "README.md").read_text()
        cls.docs = (ROOT / "docs" / "deploy-runner-vault.md").read_text()

    def test_deployment_is_manual_and_restricted_to_default_branch(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("\n  push:", self.workflow)
        self.assertNotIn("\n  pull_request:", self.workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.workflow)

    def test_private_vault_source_is_fixed_to_reviewed_main(self):
        self.assertIn("repository: aminsh35322088-ctrl/GitHub-runner-vault", self.workflow)
        self.assertIn("ref: main", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertNotIn("inputs.vault_ref", self.workflow)
        self.assertNotIn("https://github.com/aminsh35322088-ctrl/GitHub-runner-vault.git", self.workflow)

    def test_cloudflare_and_persistent_vault_keys_are_required(self):
        for name in (
            "AGENT_GITHUB_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_ACCOUNT_ID",
            "VAULT_BETTER_AUTH_SECRET",
            "VAULT_ENCRYPTION_KEY",
        ):
            with self.subTest(secret=name):
                self.assertIn(name, self.workflow)
                self.assertIn(name, self.docs)

    def test_existing_worker_name_is_rejected_by_default_launcher(self):
        self.assertIn('if [[ "$WORKER_NAME" == "runner-vault" ]]', self.workflow)
        self.assertIn("runner-vault-v2", self.workflow)

    def test_readme_badge_targets_private_source_deployment_action(self):
        self.assertIn(
            "https://github.com/aminsh35322088-ctrl/GitHub-Runner-Lab/actions/workflows/deploy-runner-vault.yml",
            self.readme,
        )
        self.assertIn("not Cloudflare's native public-repository button", self.readme)
        self.assertIn("checks out the private", self.readme)
        self.assertIn("GitHub-runner-vault", self.readme)


if __name__ == "__main__":
    unittest.main()
