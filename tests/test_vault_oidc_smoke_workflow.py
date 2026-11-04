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
        self.assertIn("VAULT_CLIENT_REPOSITORY=${{ github.repository_owner }}/GitHub-runner-vault", self.workflow)
        self.assertIn("VAULT_CLIENT_REF=$client_ref", self.workflow)
        self.assertNotIn("5712a78c7c6cff1da4dd662e506c73d459eb2527", self.workflow)
        self.assertIn('node "$client" list', self.workflow)
        self.assertIn('node "$client" use GITHUB_API_TOKEN GET https://api.github.com/user', self.workflow)
        self.assertIn('node "$client" use CLOUDFLARE_API_TOKEN GET https://api.cloudflare.com/client/v4/user/tokens/verify', self.workflow)
        self.assertIn("Secret values logged or returned to summary: **never**", self.workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.workflow)

    def test_rdc_and_tailscale_are_ready_before_optional_vault_work(self):
        rdc = self.workflow.index("      - name: Start Remote Desktop Commander")
        rdc_health = self.workflow.index("      - name: Verify connection", rdc)
        tailscale = self.workflow.index("      - name: Enable and verify Tailscale SSH", rdc_health)
        tailscale_ready = self.workflow.index('echo "TAILSCALE_HEALTH=READY"', tailscale)
        vault_install = self.workflow.index("      - name: Install Runner Credential Vault client")
        vault_connect = self.workflow.index("      - name: Connect runner to Credential Vault")
        vault_verify = self.workflow.index("      - name: Verify Vault environment reached RDC")
        self.assertLess(rdc, rdc_health)
        self.assertLess(rdc_health, tailscale)
        self.assertLess(tailscale_ready, vault_install)
        self.assertIn("steps.verify.outputs.ready == 'true' && steps.tailscale.outputs.ready == 'true'", self.workflow[vault_install:vault_connect])
        self.assertLess(vault_install, vault_connect)
        self.assertLess(vault_connect, vault_verify)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn('node "$HOME/.local/share/runner-vault/vault.mjs" list', self.workflow[vault_connect:vault_verify])
        self.assertIn("VAULT_OIDC_CONNECTION=READY", self.workflow[vault_connect:vault_verify])
        self.assertIn("Secret values exposed: **never**", self.workflow[vault_connect:vault_verify])
        self.assertIn('echo "ready=true" >> "$GITHUB_OUTPUT"', self.workflow[vault_connect:vault_verify])

    def test_post_start_vault_connection_keeps_job_oidc_and_verifier_state_aligned(self):
        runner = self.workflow.split("  runner-lab:", 1)[1].split("\n  relaunch:", 1)[0]
        job_header = runner.split("\n    steps:", 1)[0]
        self.assertNotIn("vars.RUNNER_VAULT_URL", job_header)
        self.assertIn("id-token: write", job_header)

        # Discovery runs before RDC starts and exports VAULT_URL to the job
        # environment, so the RDC process inherits it just as it did when the
        # URL came from a job-level Actions variable.
        discovery = runner.index("      - name: Discover Vault deployment")
        rdc_start = runner.index("      - name: Start Remote Desktop Commander")
        self.assertLess(discovery, rdc_start)
        self.assertIn('echo "VAULT_URL=$vault_url"', runner)
        self.assertIn('>> "$GITHUB_ENV"', runner)

        verify_step = runner.split("      - name: Verify Vault environment reached RDC", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("VAULT_CONNECT_STATE: ${{ steps.vault-connect.outputs.ready }}", verify_step)
        verifier = (ROOT / "scripts" / "verify-vault-runtime.sh").read_text()
        self.assertIn('if [[ "${VAULT_CONNECT_STATE:-}" != "true" ]]', verifier)
        self.assertIn('env.get("ACTIONS_ID_TOKEN_REQUEST_URL")', verifier)
        self.assertIn('env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")', verifier)

    def test_optional_vault_network_steps_have_bounded_timeouts_and_degrade_gracefully(self):
        install = self.workflow.split("      - name: Install Runner Credential Vault client", 1)[1].split("      - name: Connect runner to Credential Vault", 1)[0]
        connect = self.workflow.split("      - name: Connect runner to Credential Vault", 1)[1].split("      - name: Verify Vault environment reached RDC", 1)[0]
        verify = self.workflow.split("      - name: Verify Vault environment reached RDC", 1)[1].split("      - name: Persist verified first-time RDC identity", 1)[0]
        self.assertIn("timeout-minutes: 2", install)
        self.assertIn("continue-on-error: true", install)
        self.assertIn("timeout-minutes: 1", connect)
        self.assertIn("VAULT_REQUEST_TIMEOUT_MS: '5000'", connect)
        self.assertIn("continue-on-error: true", connect)
        self.assertIn("continue-on-error: true", verify)
        self.assertIn("Vault operations remain unavailable", verify)

    def test_vault_client_fetch_has_bounded_anonymous_and_token_fallback(self):
        fetch = (ROOT / "scripts" / "fetch-vault-client.sh").read_text()
        self.assertIn("--connect-timeout 5 --max-time 15", fetch)
        self.assertIn("timeout --signal=TERM --kill-after=5s 20s", fetch)
        self.assertIn("unset VAULT_CLIENT_GITHUB_TOKEN", fetch)

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

    def test_vault_is_discovered_from_deployment_json_not_actions_variables(self):
        self.assertNotIn("vars.RUNNER_VAULT_URL", self.workflow)
        self.assertNotIn("vars.RUNNER_VAULT_REPOSITORY", self.workflow)
        self.assertNotIn("vars.RUNNER_VAULT_CLIENT_REF", self.workflow)
        self.assertNotIn("RUNNER_VAULT_URL", self.workflow)
        self.assertNotIn("RUNNER_VAULT_REPOSITORY", self.workflow)
        self.assertNotIn("RUNNER_VAULT_CLIENT_REF", self.workflow)
        discovery_url = "https://raw.githubusercontent.com/${{ github.repository_owner }}/GitHub-runner-vault/main/deployment.json"
        # Every job that consumes the Vault discovers it the same way.
        self.assertEqual(3, self.workflow.count("id: vault-discovery"))
        self.assertEqual(3, self.workflow.count(discovery_url))
        self.assertIn("curl -fsSL --max-time 10", self.workflow)
        self.assertIn("steps.vault-discovery.outputs.configured == 'true'", self.workflow)
        # The discovered values still pass the original validations before use.
        validate = self.workflow.split("      - name: Validate Vault configuration", 1)[1].split("      - name: Fetch the pinned Vault client", 1)[0]
        self.assertIn("^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$", validate)
        self.assertIn("^[0-9a-f]{40}$", validate)
        self.assertIn("VAULT_URL must be an HTTPS origin with no path, query or credentials", validate)

    def test_smoke_test_does_not_boot_the_persistent_runner_job(self):
        self.assertIn("if: " + "$" + "{{ github.event_name != 'workflow_dispatch' || inputs.action == 'Start / Resume' }}", self.workflow)
        self.assertIn("Vault smoke test", self.readme)
        self.assertIn("The existing **Remote Desktop Commander Lab → Vault smoke test** action", self.readme)


if __name__ == "__main__":
    unittest.main()
