import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from discover_vault import validate_deployment


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rdc-lab.yml"
README = ROOT / "README.md"


class VaultDiscoveryInputTests(unittest.TestCase):
    OWNER = "aminsh35322088-ctrl"
    SHA = "a" * 40

    def test_accepts_a_valid_workers_origin_and_immutable_revision(self):
        values = validate_deployment(
            {"url": "https://runner-vault.example.workers.dev", "clientRef": self.SHA},
            self.OWNER,
        )
        self.assertEqual(values, {
            "VAULT_URL": "https://runner-vault.example.workers.dev",
            "VAULT_CLIENT_REPOSITORY": self.OWNER + "/GitHub-runner-vault",
            "VAULT_CLIENT_REF": self.SHA,
        })

    def test_rejects_environment_injection_in_url_or_sha(self):
        for malicious in (
            "https://vault.example.workers.dev\nEXTRA=value",
            "https://vault.example.workers.dev\rGITHUB_TOKEN=stolen",
        ):
            with self.subTest(value=repr(malicious)):
                with self.assertRaises(ValueError):
                    validate_deployment({"url": malicious, "clientRef": self.SHA}, self.OWNER)
        with self.assertRaises(ValueError):
            validate_deployment({"url": "https://vault.example.workers.dev", "clientRef": self.SHA + "\nPWNED=1"}, self.OWNER)

    def test_rejects_non_workers_dev_origins(self):
        for url in (
            "https://attacker.example",
            "https://runner-vault.example.workers.dev.evil.example",
            "https://runner-vault.example.workers.dev:8443",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_deployment({"url": url, "clientRef": self.SHA}, self.OWNER)

    def test_rejects_non_origin_urls_and_credentials(self):
        for url in (
            "http://vault.example.workers.dev",
            "https://user:pass@vault.example.workers.dev",
            "https://vault.example.workers.dev/path",
            "https://vault.example.workers.dev/?query=1",
            "https://vault.example.workers.dev/#fragment",
            " https://vault.example.workers.dev",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_deployment({"url": url, "clientRef": self.SHA}, self.OWNER)

    def test_rejects_invalid_sha_document_shape_and_owner(self):
        for payload in (
            {"url": "https://vault.example.workers.dev", "clientRef": "g" * 40},
            {"url": "https://vault.example.workers.dev", "clientRef": self.SHA.upper()},
            [],
            {"url": "https://vault.example.workers.dev"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_deployment(payload, self.OWNER)
        with self.assertRaises(ValueError):
            validate_deployment({"url": "https://vault.example.workers.dev", "clientRef": self.SHA}, "owner\nBAD=1")


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
        discovery_helper = (ROOT / "scripts" / "discover_vault.py").read_text()
        self.assertIn('f"{owner}/GitHub-runner-vault"', discovery_helper)
        self.assertIn('"VAULT_CLIENT_REF": client_ref', discovery_helper)
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
        self.assertIn("run: python3 scripts/discover_vault.py", runner)
        self.assertNotIn('echo "VAULT_URL=$vault_url"', runner)

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
        # Every job that consumes the Vault discovers it the same way.
        self.assertEqual(3, self.workflow.count("id: vault-discovery"))
        self.assertEqual(3, self.workflow.count("run: python3 scripts/discover_vault.py"))
        self.assertIn("steps.vault-discovery.outputs.configured == 'true'", self.workflow)
        # Shared discovery validates every untrusted value before writing GITHUB_ENV.
        helper = (ROOT / "scripts" / "discover_vault.py").read_text()
        self.assertIn("validate_deployment(payload, owner)", helper)
        self.assertIn("SHA_RE.fullmatch(client_ref)", helper)
        self.assertIn("parsed.scheme != \"https\"", helper)
        self.assertLess(helper.index("values = validate_deployment"), helper.index('with open(env_path'))
        self.assertIn("MAX_DOCUMENT_BYTES", helper)

    def test_smoke_test_does_not_boot_the_persistent_runner_job(self):
        self.assertIn("if: " + "$" + "{{ github.event_name != 'workflow_dispatch' || inputs.action == 'Start / Resume' }}", self.workflow)
        self.assertIn("Vault smoke test", self.readme)
        self.assertIn("The existing **Remote Desktop Commander Lab → Vault smoke test** action", self.readme)


if __name__ == "__main__":
    unittest.main()
