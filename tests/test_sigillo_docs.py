import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
AGENTS = (ROOT / "AGENTS.md").read_text()
SIGILLO_DOC = (ROOT / "docs" / "sigillo.md").read_text()
SIGILLO_SKILL = (ROOT / "skills" / "sigillo" / "SKILL.md").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "rdc-lab.yml").read_text()


class SigilloDocumentationTests(unittest.TestCase):
    def test_runner_bootstrap_uses_actions_secret_and_optional_api_origin(self):
        step = WORKFLOW.split(
            "      - name: Install and authenticate Sigillo CLI (optional)", 1
        )[1].split("\n      - name:", 1)[0]

        self.assertIn("SIGILLO_TOKEN: ${{ secrets.SIGILLO_API_TOKEN }}", step)
        self.assertIn("SIGILLO_API_URL: ${{ vars.SIGILLO_API_URL }}", step)
        self.assertIn('SIGILLO_API_URL="https://sigillo.dev"', step)
        self.assertIn("https://", step)
        self.assertIn('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo me', step)
        self.assertIn('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo login --scope /', step)
        self.assertIn('echo "SIGILLO_CLI_READY=true"', step)
        self.assertIn('echo "SIGILLO_CLI_READY=false"', step)

        preflight = step.index('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo me')
        login = step.index('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo login --scope /')
        postflight = step.index('! sigillo me >/dev/null 2>"$auth_err"')
        ready = step.index('echo "SIGILLO_CLI_READY=true"')
        self.assertLess(preflight, login)
        self.assertLess(login, postflight)
        self.assertLess(postflight, ready)

    def test_sigillo_is_documented_as_optional_and_non_blocking(self):
        self.assertIn("SIGILLO_API_TOKEN", README)
        self.assertIn("SIGILLO_API_URL", README)
        self.assertIn("must not block RDC or Tailscale", README)
        self.assertIn("optional", SIGILLO_DOC.lower())
        self.assertIn("must not block RDC or Tailscale", AGENTS)

    def test_sigillo_and_runner_vault_security_models_are_distinguished(self):
        for document in (README, AGENTS, SIGILLO_DOC):
            self.assertIn("Runner Credential Vault", document)
        self.assertIn("proxy-only", README)
        self.assertIn("Sigillo is not proxy-only", SIGILLO_DOC)
        self.assertIn("child process", SIGILLO_DOC)

    def test_agent_instructions_protect_cli_config_and_secret_values(self):
        for document in (AGENTS, SIGILLO_DOC, SIGILLO_SKILL):
            self.assertIn("~/.sigillo", document)
        self.assertIn("Never read", SIGILLO_DOC)
        self.assertIn("Never read", AGENTS)
        self.assertIn("never echo", SIGILLO_DOC.lower())

    def test_vendored_skill_keeps_upstream_frontmatter_and_runner_notes(self):
        self.assertTrue(SIGILLO_SKILL.startswith("---\n"))
        self.assertIn("name: sigillo", SIGILLO_SKILL)
        self.assertIn("ALWAYS fetch the latest README", SIGILLO_SKILL)
        self.assertIn("Never use `sigillo run` output as secret data", SIGILLO_SKILL)
        self.assertIn("GitHub Runner Lab integration notes", SIGILLO_SKILL)
        self.assertIn("skills add remorses/sigillo", (ROOT / "skills" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
