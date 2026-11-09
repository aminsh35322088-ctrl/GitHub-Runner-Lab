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
            "      - name: Install and authenticate Sigillo CLI", 1
        )[1].split("\n      - name:", 1)[0]

        self.assertIn("SIGILLO_TOKEN: ${{ secrets.SIGILLO_API_TOKEN }}", step)
        self.assertIn("SIGILLO_API_URL: ${{ vars.SIGILLO_API_URL }}", step)
        self.assertIn('SIGILLO_API_URL="https://sigillo.dev"', step)
        self.assertIn('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo me', step)
        self.assertIn('SIGILLO_TOKEN="$SIGILLO_TOKEN" sigillo login --scope /', step)
        self.assertIn('echo "SIGILLO_CLI_READY=true"', step)

    def test_agent_github_credential_is_retrieved_from_sigillo(self):
        step = WORKFLOW.split(
            "      - name: Configure Agent GitHub access from Sigillo", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("SIGILLO_TOKEN: ${{ secrets.SIGILLO_API_TOKEN }}", step)
        self.assertIn(
            'sigillo run --project "$SIGILLO_PROJECT_ID" --env "$SIGILLO_ENVIRONMENT"',
            step,
        )
        self.assertIn('printf "%s" "${GITHUB_API_TOKEN:', step)
        self.assertIn("env -u GITHUB_API_TOKEN ./scripts/agent-github.sh install", step)
        self.assertIn("./scripts/agent-github.sh verify", step)
        self.assertNotIn('GITHUB_API_TOKEN: ${{ secrets.', WORKFLOW)

    def test_sigillo_smoke_test_exercises_agent_github_bootstrap_in_temporary_home(self):
        step = WORKFLOW.split(
            "      - name: Verify Agent GitHub credential from Sigillo", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn('sigillo run --project "$SIGILLO_PROJECT_ID" --env "$SIGILLO_ENVIRONMENT"', step)
        self.assertIn('printf "%s" "${GITHUB_API_TOKEN:', step)
        self.assertIn("agent-github.sh install", step)
        self.assertIn("agent-github.sh verify", step)
        self.assertIn("agent-github.sh remove", step)
        self.assertIn('GH_CONFIG_DIR="$tmpdir/gh"', step)
        self.assertIn("trap 'rm -rf \"$tmpdir\"' EXIT", step)

    def test_github_actions_api_remains_independent_from_sigillo(self):
        self.assertIn("secrets.ACTIONS_WATCHDOG_TOKEN || github.token", WORKFLOW)
        self.assertIn("GH_TOKEN: ${{ secrets.ACTIONS_WATCHDOG_TOKEN || github.token }}", WORKFLOW)
        self.assertNotIn("SIGILLO_TOKEN", WORKFLOW.split("      - name: Queue Successor Run", 1)[1])
        self.assertNotIn("GITHUB_API_TOKEN", WORKFLOW.split("      - name: Queue Successor Run", 1)[1])

    def test_runner_handoff_outputs_remain_wired_to_relaunch(self):
        runner = WORKFLOW.split("  runner-lab:", 1)[1].split("\n  relaunch:", 1)[0]
        self.assertIn("configured: ${{ steps.verify.outputs.ready }}", runner)
        self.assertIn("queued: ${{ steps.handover.outputs.queued }}", runner)
        self.assertIn("needs.runner-lab.outputs.configured", WORKFLOW)
        self.assertIn("needs.runner-lab.outputs.queued", WORKFLOW)

    def test_sigillo_is_required_for_agent_credentials_but_smoke_test_is_separate(self):
        self.assertIn("SIGILLO_API_TOKEN", README)
        self.assertIn("GITHUB_API_TOKEN", README)
        self.assertIn("SIGILLO_API_TOKEN", AGENTS)
        self.assertIn("Sigillo smoke test", WORKFLOW)
        self.assertIn("sigillo-smoke-test:", WORKFLOW)
        self.assertIn("inputs.action == 'Sigillo smoke test'", WORKFLOW)
        self.assertIn("must not depend on Sigillo", AGENTS)

    def test_sigillo_docs_preserve_secret_handling_boundaries(self):
        for document in (AGENTS, SIGILLO_DOC, SIGILLO_SKILL):
            self.assertIn("~/.sigillo", document)
        self.assertIn("Never read", SIGILLO_DOC)
        self.assertIn("sigillo run", SIGILLO_DOC)
        self.assertIn("stdin", SIGILLO_DOC)
        self.assertIn("GitHub Actions API", SIGILLO_DOC)
        self.assertIn("Never use `sigillo run` output as secret data", SIGILLO_SKILL)

    def test_vendored_skill_keeps_upstream_frontmatter_and_runner_notes(self):
        self.assertTrue(SIGILLO_SKILL.startswith("---\n"))
        self.assertIn("name: sigillo", SIGILLO_SKILL)
        self.assertIn("ALWAYS fetch the latest README", SIGILLO_SKILL)
        self.assertIn("GitHub Runner Lab integration notes", SIGILLO_SKILL)
        self.assertIn("skills add remorses/sigillo", (ROOT / "skills" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
