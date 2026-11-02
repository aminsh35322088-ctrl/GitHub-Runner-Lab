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

    def test_smoke_test_fetches_pinned_client_and_lists_metadata_only(self):
        self.assertIn("./scripts/fetch-vault-client.sh", self.workflow)
        self.assertIn("VAULT_CLIENT_REPOSITORY: ${{ vars.RUNNER_VAULT_REPOSITORY }}", self.workflow)
        self.assertIn("VAULT_CLIENT_REF: ${{ vars.RUNNER_VAULT_CLIENT_REF }}", self.workflow)
        self.assertNotIn("5712a78c7c6cff1da4dd662e506c73d459eb2527", self.workflow)
        self.assertIn('node "$client" list', self.workflow)
        self.assertIn('node "$client" use GITHUB_API_TOKEN GET https://api.github.com/user', self.workflow)
        self.assertIn('node "$client" use CLOUDFLARE_API_TOKEN GET https://api.cloudflare.com/client/v4/user/tokens/verify', self.workflow)
        self.assertIn("Secret values logged or returned to summary: **never**", self.workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.workflow)

    def test_runner_job_connects_to_vault_automatically_before_starting_rdc(self):
        start = self.workflow.index("      - name: Connect runner to Credential Vault")
        rdc = self.workflow.index("      - name: Start Remote Desktop Commander", start)
        self.assertLess(start, rdc)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn('node "$HOME/.local/share/runner-vault/vault.mjs" list', self.workflow[start:rdc])
        self.assertIn("VAULT_OIDC_CONNECTION=READY", self.workflow[start:rdc])
        self.assertIn("Secret values exposed: **never**", self.workflow[start:rdc])
        self.assertIn('echo "ready=true" >> "$GITHUB_OUTPUT"', self.workflow[start:rdc])

    def test_cloudflare_smoke_test_supports_account_tokens_without_logging_responses(self):
        self.assertIn("CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}", self.workflow)
        self.assertIn("accounts/${account_id}/tokens/verify", self.workflow)
        self.assertIn("Raw responses and credential values were not logged", self.workflow)
        self.assertIn("CLOUDFLARE_TOKEN_TYPE=$cf_token_type", self.workflow)

    def test_smoke_test_reports_only_safe_registry_metadata(self):
        self.assertIn("### Safe credential metadata", self.workflow)
        self.assertIn(".name", self.workflow)
        self.assertIn(".service", self.workflow)
        self.assertIn(".delivery", self.workflow)
        self.assertIn(".validation", self.workflow)
        self.assertIn(".lifetime", self.workflow)
        self.assertNotIn(".value_encrypted", self.workflow)
        self.assertNotIn(".value", self.workflow)

    def test_smoke_test_does_not_boot_the_persistent_runner_job(self):
        self.assertIn("if: " + "$" + "{{ github.event_name != 'workflow_dispatch' || inputs.action == 'Start / Resume' }}", self.workflow)
        self.assertIn("Vault smoke test", self.readme)
        self.assertIn("The existing **Remote Desktop Commander Lab → Vault smoke test** action", self.readme)


if __name__ == "__main__":
    unittest.main()
