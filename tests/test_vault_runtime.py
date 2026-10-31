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

    def test_fails_closed_when_oidc_requester_is_not_in_rdc_environment(self):
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
                self.assertEqual(result.returncode, 1)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_URL=MISSING", result.stdout)
                self.assertIn("VAULT_RDC_OIDC_REQUEST_TOKEN=MISSING", result.stdout)
                self.assertNotIn("SYNTHETIC", result.stdout + result.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_disabled_vault_is_a_clean_optional_state(self):
        env = os.environ.copy()
        env.pop("VAULT_URL", None)
        result = self.run_verifier(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_RUNTIME=DISABLED", result.stdout)


if __name__ == "__main__":
    unittest.main()
