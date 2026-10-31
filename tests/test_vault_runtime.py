import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-vault-runtime.sh"


class VaultRuntimeVerifierTests(unittest.TestCase):
    def start_process(self, env):
        return subprocess.Popen(["bash", "-c", "exec sleep 30"], env=env)

    def run_verifier(self, env):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

    def test_reports_ready_without_printing_oidc_values(self):
        with tempfile.TemporaryDirectory(prefix="vault-runtime-") as tmp:
            root = Path(tmp)
            client = root / "vault"
            client.write_text("#!/bin/sh\nexit 0\n")
            client.chmod(0o700)
            pid_file = root / "rdc.pid"
            url = "https://vault.example.workers.dev"
            oidc_url = "https://pipelines.actions.githubusercontent.com/opaque"
            oidc_token = "SYNTHETIC_OIDC_REQUEST_SECRET_MUST_NOT_PRINT"
            env = os.environ.copy()
            env.update({
                "VAULT_URL": url,
                "ACTIONS_ID_TOKEN_REQUEST_URL": oidc_url,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": oidc_token,
            })
            process = self.start_process(env)
            try:
                pid_file.write_text(str(process.pid))
                env.update({
                    "VAULT_CLIENT_BIN": str(client),
                    "RDC_PID_FILE": str(pid_file),
                })
                result = self.run_verifier(env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("VAULT_RDC_VAULT_URL=READY", result.stdout)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_URL=READY", result.stdout)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_TOKEN=READY", result.stdout)
                self.assertIn("VAULT_RUNTIME=READY", result.stdout)
                self.assertNotIn(oidc_url, result.stdout + result.stderr)
                self.assertNotIn(oidc_token, result.stdout + result.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_reports_degraded_without_failing_when_oidc_requester_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="vault-runtime-") as tmp:
            root = Path(tmp)
            client = root / "vault"
            client.write_text("#!/bin/sh\nexit 0\n")
            client.chmod(0o700)
            pid_file = root / "rdc.pid"
            env = os.environ.copy()
            env.update({"VAULT_URL": "https://vault.example.workers.dev"})
            process = self.start_process(env)
            try:
                pid_file.write_text(str(process.pid))
                env.update({
                    "VAULT_CLIENT_BIN": str(client),
                    "RDC_PID_FILE": str(pid_file),
                })
                result = self.run_verifier(env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_URL=MISSING", result.stdout)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_TOKEN=MISSING", result.stdout)
                self.assertIn("VAULT_RUNTIME=DEGRADED", result.stdout)
                self.assertNotIn("SYNTHETIC", result.stdout + result.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_missing_client_degrades_without_blocking_startup(self):
        with tempfile.TemporaryDirectory(prefix="vault-runtime-") as tmp:
            root = Path(tmp)
            env = os.environ.copy()
            env.update({
                "VAULT_URL": "https://vault.example.workers.dev",
                "VAULT_CLIENT_BIN": str(root / "missing-vault"),
                "RDC_PID_FILE": str(root / "rdc.pid"),
            })
            result = self.run_verifier(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("VAULT_RDC_CLIENT=MISSING", result.stdout)
            self.assertIn("VAULT_RUNTIME=DEGRADED", result.stdout)

    def test_degraded_state_is_exposed_as_a_step_output_and_summary(self):
        with tempfile.TemporaryDirectory(prefix="vault-runtime-") as tmp:
            root = Path(tmp)
            output = root / "github-output"
            summary = root / "github-summary"
            env = os.environ.copy()
            env.update({
                "VAULT_URL": "https://vault.example.workers.dev",
                "VAULT_CLIENT_BIN": str(root / "missing-vault"),
                "RDC_PID_FILE": str(root / "rdc.pid"),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            })
            result = self.run_verifier(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("state=degraded", output.read_text())
            self.assertIn("Status: **DEGRADED**", summary.read_text())

    def test_disabled_vault_is_a_clean_optional_state(self):
        env = os.environ.copy()
        env.pop("VAULT_URL", None)
        result = self.run_verifier(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_RUNTIME=DISABLED", result.stdout)



class VaultWorkflowIsolationTests(unittest.TestCase):
    def test_prewarm_step_scrubs_oidc_requester_before_launch(self):
        workflow = (ROOT / ".github" / "workflows" / "rdc-lab.yml").read_text()
        marker = "- name: Prewarm Agent Toolchain and Keep RDC Lab Alive"
        block = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
        scrub = "unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN"
        self.assertIn(scrub, block)
        self.assertLess(block.index(scrub), block.index("./scripts/agent-prewarm.sh --background"))
        self.assertLess(block.index(scrub), block.index("./scripts/keepalive.sh"))

if __name__ == "__main__":
    unittest.main()
