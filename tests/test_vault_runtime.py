from __future__ import annotations

import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-vault-runtime.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "rdc-lab.yml"


class VaultRuntimeVerifierTests(unittest.TestCase):
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

    def test_agent_guidance_documents_the_live_broker_boundary(self):
        guidance = " ".join((ROOT / "AGENTS.md").read_text().split())
        doc = " ".join((ROOT / "docs" / "credential-vault.md").read_text().split())
        for marker in ("runner-vault-broker", "SO_PEERCRED", "vault status", "vault serve", "rootless Podman"):
            self.assertIn(marker, guidance)
            self.assertIn(marker, doc)
        self.assertNotIn("broker is not yet implemented", guidance.lower())
        self.assertNotIn("Agent-side Vault CLI operations are intentionally unavailable", guidance)

    def test_identity_setup_checks_all_rootless_podman_prerequisites(self):
        setup = (ROOT / "scripts" / "setup-runner-identities.sh").read_text()
        for command in ("setfacl", "podman", "newuidmap", "newgidmap", "slirp4netns", "fuse-overlayfs"):
            self.assertIn(f"command -v {command}", setup)
        self.assertIn('apt-get install -y --no-install-recommends "${SYSTEM_PACKAGES[@]}"', setup)

    def test_disabled_vault_is_a_clean_optional_state(self):
        env = os.environ.copy()
        env.pop("VAULT_URL", None)
        result = self.run_verifier(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_RUNTIME=DISABLED", result.stdout)

    def test_missing_broker_readiness_degrades_without_blocking_connectivity(self):
        env = os.environ.copy()
        env.update({
            "VAULT_URL": "https://vault.example.workers.dev",
            "VAULT_CONNECT_STATE": "false",
        })
        result = self.run_verifier(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_RUNTIME=DEGRADED", result.stdout)
        self.assertIn("Broker status/registry verification", result.stdout)
        self.assertNotIn("VAULT_RUNTIME=READY", result.stdout)

    def test_any_degraded_verification_stops_the_broker_and_removes_its_socket(self):
        verifier = SCRIPT.read_text()
        stop = (ROOT / "scripts" / "stop-vault-broker.sh").read_text()
        workflow = WORKFLOW.read_text()
        self.assertIn('sudo -n ./scripts/stop-vault-broker.sh', verifier)
        self.assertIn('Broker endpoint removal could not be confirmed', verifier)
        self.assertIn('sudo -n rm -f -- "$socket" "$pid_file"', verifier)
        self.assertIn("argv[1] == entrypoint and argv[2] == 'serve'", stop)
        self.assertIn("argv.index('--socket')", stop)
        self.assertIn('sudo -n ./scripts/stop-vault-broker.sh', workflow)
        self.assertIn('Broker socket revocation could not be confirmed', workflow)
        self.assertIn('rm -f -- "$PID_FILE" "$SOCKET"', stop)

    def test_broker_stop_terminates_only_the_matching_service_process(self):
        stop_script = ROOT / "scripts" / "stop-vault-broker.sh"
        with tempfile.TemporaryDirectory(prefix="vault-broker-stop-") as tmp:
            root = Path(tmp)
            home = root / "broker-home"
            home.mkdir()
            entrypoint = home / "vault_broker.py"
            entrypoint.write_text("import time\ntime.sleep(60)\n")
            socket_path = root / "broker.sock"
            pid_file = root / "broker.pid"
            socket_path.write_text("placeholder")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nif [ \"$1\" = -u ]; then echo 0; else exec /usr/bin/id \"$@\"; fi\n")
            fake_id.chmod(0o755)
            broker_user = pwd.getpwuid(os.getuid()).pw_name
            process = subprocess.Popen([
                sys.executable, str(entrypoint), "serve", "--socket", str(socket_path),
                "--pid-file", str(pid_file), "--bootstrap-stdin",
            ])
            pid_file.write_text(f"{process.pid}\n")
            try:
                result = subprocess.run(
                    ["bash", str(stop_script)],
                    env={**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                         "VAULT_BROKER_USER": broker_user, "VAULT_BROKER_HOME": str(home),
                         "VAULT_BROKER_SOCKET": str(socket_path),
                         "VAULT_BROKER_PID_FILE": str(pid_file)},
                    capture_output=True, text=True, timeout=8,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNotNone(process.poll())
                self.assertFalse(socket_path.exists())
                self.assertFalse(pid_file.exists())
                self.assertIn("VAULT_BROKER=STOPPED", result.stdout)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=2)

    def test_broker_stop_does_not_kill_a_stale_pid_target(self):
        stop_script = ROOT / "scripts" / "stop-vault-broker.sh"
        with tempfile.TemporaryDirectory(prefix="vault-broker-stale-pid-") as tmp:
            root = Path(tmp)
            home = root / "broker-home"
            home.mkdir()
            (home / "vault_broker.py").write_text("import time\ntime.sleep(60)\n")
            socket_path = root / "broker.sock"
            pid_file = root / "broker.pid"
            socket_path.write_text("placeholder")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nif [ \"$1\" = -u ]; then echo 0; else exec /usr/bin/id \"$@\"; fi\n")
            fake_id.chmod(0o755)
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            pid_file.write_text(f"{unrelated.pid}\n")
            try:
                result = subprocess.run(
                    ["bash", str(stop_script)],
                    env={**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                         "VAULT_BROKER_USER": pwd.getpwuid(os.getuid()).pw_name, "VAULT_BROKER_HOME": str(home),
                         "VAULT_BROKER_SOCKET": str(socket_path),
                         "VAULT_BROKER_PID_FILE": str(pid_file)},
                    capture_output=True, text=True, timeout=8,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(unrelated.poll(), "stale PID file caused an unrelated process to be killed")
                self.assertFalse(socket_path.exists())
                self.assertFalse(pid_file.exists())
            finally:
                unrelated.terminate(); unrelated.wait(timeout=3)

    def test_broker_verifier_checks_separate_uids_socket_and_rootless_podman(self):
        verifier = SCRIPT.read_text()
        for marker in (
            "SO_PEERCRED",
            "CLIENT_GROUP",
            "AGENT_UID",
            "BROKER_UID",
            "VAULT_RDC_SOCKET",
            "VAULT_RDC_CONTAINER_ENGINE=ROOTLESS_PODMAN",
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            "BROKER_BOOTSTRAP_OIDC_TOKEN",
            "BROKER_OIDC_REQUEST_TOKEN",
            "Path(f\"/proc/{pid}/environ\")",
            "Path(f\"/proc/{pid}/cmdline\")",
            "RDC_STATE_KEY",
            "GH_TOKEN",
            "AGENT_GITHUB_TOKEN",
            '"$AGENT_HOME/.local/bin/vault" status',
            '"$AGENT_HOME/.local/bin/vault" list',
        ):
            self.assertTrue(marker in verifier or marker in (ROOT / "scripts" / "vault_broker.py").read_text(), marker)
        self.assertIn("docker|sudo", verifier)
        self.assertIn("0660", verifier)
        self.assertIn("0750", verifier)

    def test_broker_has_no_session_export_or_shell_operation(self):
        broker = (ROOT / "scripts" / "vault_broker.py").read_text()
        for forbidden in ("session.read", "session.export", "shell.exec", "subprocess.Popen", "os.system"):
            self.assertNotIn(forbidden, broker)
        self.assertIn("SO_PEERCRED", broker)
        self.assertIn("OPERATION_NOT_ALLOWED", broker)
        self.assertIn("tokens.list", broker)
        self.assertIn("integrations.list", broker)
        self.assertIn("registrations.create", broker)
        self.assertIn("credential.use", broker)

    def test_agent_launcher_uses_an_explicit_environment_allowlist(self):
        launcher = (ROOT / "scripts" / "run-as-rdc-agent.sh").read_text()
        self.assertIn("env -i", launcher)
        self.assertIn("sudo -n -u", launcher)
        self.assertIn("AGENT_CONTAINER_ENGINE=podman", launcher)
        self.assertIn('GITHUB_WORKSPACE="$AGENT_LAB"', launcher)
        self.assertIn('RDC_PID_FILE="$RDC_AGENT_HOME/.run/rdc.pid"', launcher)
        self.assertIn('RDC_LOG_FILE="$RDC_AGENT_HOME/.run/rdc.log"', launcher)
        self.assertIn('RDC_HEALTH_FILE="$RDC_AGENT_HOME/.run/rdc-health.json"', launcher)
        self.assertIn('npm_config_cache="${AGENT_NPM_CACHE_DIR:-/home/runner/agent-npm-cache}"', launcher)
        self.assertIn('cd "$1"', launcher)
        self.assertIn("RDC_STATE_KEY", launcher)
        self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_TOKEN="$ACTIONS_ID_TOKEN_REQUEST_TOKEN"', launcher)
        self.assertIn('AGENT_EXTRA_ENV+=(\"AGENT_GITHUB_DOCKER_HELPER=', launcher)

    def test_agent_source_snapshot_excludes_git_and_is_root_owned_read_only(self):
        setup = (ROOT / "scripts" / "setup-runner-identities.sh").read_text()
        self.assertIn('setfacl -R -m "u:${AGENT_USER}:---" "$RUNNER_HOME"', setup)
        self.assertIn('d:u:${AGENT_USER}:---', setup)
        self.assertIn("--exclude='./.git'", setup)
        self.assertIn('chown -R "root:$AGENT_USER" "$AGENT_LAB"', setup)
        self.assertIn('find "$AGENT_LAB" -type d -exec chmod 0550', setup)
        self.assertIn('find "$AGENT_LAB" -type f -perm /111 -exec chmod 0550', setup)
        self.assertNotIn('setfacl -R -m "u:${AGENT_USER}:rwX" "$WORKSPACE"', setup)
        shared_paths = setup.split('shared_paths=(', 1)[1].split(')', 1)[0]
        self.assertNotIn('"$WORKSPACE"', shared_paths)

    def test_rdc_runtime_artifacts_are_private_to_the_agent_home(self):
        launcher = (ROOT / "scripts" / "run-as-rdc-agent.sh").read_text()
        start = (ROOT / "scripts" / "start-rdc.sh").read_text()
        health = (ROOT / "scripts" / "health.sh").read_text()
        supervisor = (ROOT / "scripts" / "rdc-supervisor.mjs").read_text()
        keepalive = (ROOT / "scripts" / "keepalive.sh").read_text()
        verifier = (ROOT / "scripts" / "verify-vault-runtime.sh").read_text()
        self.assertIn('RDC_PID_FILE="$RDC_AGENT_HOME/.run/rdc.pid"', launcher)
        self.assertIn('RDC_HEALTH_FILE="$RDC_AGENT_HOME/.run/rdc-health.json"', launcher)
        self.assertIn('chmod 0700 "$HOME/.run"', start)
        self.assertIn("os.path.expanduser('~/.run/rdc.pid')", health)
        self.assertIn("join(homedir(), '.run', 'rdc-health.json')", supervisor)
        self.assertIn('RDC_PID_FILE="$PID_FILE"', keepalive)
        self.assertIn('"$AGENT_HOME/.run/rdc.pid"', verifier)
        self.assertNotIn('/tmp/rdc.pid', verifier)

    def test_runner_identity_setup_removes_rootful_docker_from_agent(self):
        setup = (ROOT / "scripts" / "setup-runner-identities.sh").read_text()
        self.assertIn("usermod -G \"$CLIENT_GROUP\" \"$AGENT_USER\"", setup)
        self.assertIn("id -nG \"$AGENT_USER\"", setup)
        self.assertIn("/usr/bin/podman run --userns=keep-id", setup)
        self.assertIn("runuser -u \"$AGENT_USER\"", setup)
        self.assertNotIn("usermod -aG docker", setup)
        self.assertIn("runner-vault-broker", setup)
        self.assertIn('npm_config_cache=/home/runner/agent-npm-cache', setup)
        self.assertIn('setfacl -R -m "u:${AGENT_USER}:---" /opt/hostedtoolcache', setup)
        self.assertIn('find /opt/hostedtoolcache -type d -exec setfacl -m "d:u:${AGENT_USER}:---"', setup)
        self.assertIn("AGENT_LAB", setup)

    def test_agent_bootstrap_uses_trusted_runner_package_flag_not_sudo(self):
        bootstrap = (ROOT / "scripts" / "agent-bootstrap.sh").read_text()
        self.assertIn('AGENT_SYSTEM_PACKAGES_READY:-false', bootstrap)
        self.assertIn('exit 21', bootstrap)
        self.assertNotIn('sudo apt-get', bootstrap)
        self.assertNotIn('mapfile -t PACKAGES', bootstrap)
        trusted = (ROOT / "scripts" / "install-agent-system-packages.sh").read_text()
        self.assertIn('lab_toolset.py" packages full', trusted)
        self.assertIn('apt-get install', trusted)
        self.assertNotIn('RDC Agent', trusted)

    def test_degraded_state_is_exposed_as_a_step_output_and_summary(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="vault-runtime-") as tmp:
            root = Path(tmp)
            output = root / "github-output"
            summary = root / "github-summary"
            env = os.environ.copy()
            env.update({
                "VAULT_URL": "https://vault.example.workers.dev",
                "VAULT_CONNECT_STATE": "false",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            })
            result = self.run_verifier(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("state=degraded", output.read_text())
            self.assertIn("Status: **DEGRADED**", summary.read_text())


class VaultWorkflowIsolationTests(unittest.TestCase):
    def test_rdc_direct_start_refuses_runner_uid_and_scrubs_oidc_first(self):
        script = (ROOT / "scripts" / "start-rdc.sh").read_text()
        scrub = "unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN"
        self.assertIn(scrub, script)
        self.assertLess(script.index(scrub), script.index("nohup env"))
        self.assertIn('id -un', script)
        self.assertIn("dedicated unprivileged Agent account", script)

    def test_workflow_runs_rdc_only_through_agent_wrapper(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("run-as-rdc-agent.sh ./scripts/start-rdc.sh", workflow)
        self.assertIn("run-as-rdc-agent.sh ./scripts/bootstrap-rdc.sh", workflow)
        self.assertIn("run-as-rdc-agent.sh ./scripts/health.sh", workflow)
        self.assertIn("Provision isolated RDC Agent and Vault Broker identities", workflow)

    def test_prewarm_runs_as_agent_and_job_oidc_is_not_inherited(self):
        workflow = WORKFLOW.read_text()
        marker = "- name: Prewarm Agent Toolchain and Keep RDC Lab Alive"
        block = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
        scrub = "unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN"
        self.assertIn(scrub, block)
        self.assertIn("run-as-rdc-agent.sh ./scripts/agent-prewarm.sh --background", block)
        self.assertLess(block.index(scrub), block.index("run-as-rdc-agent.sh"))

    def test_keepalive_scrubs_oidc_and_uses_agent_identity_wrapper(self):
        script = (ROOT / "scripts" / "keepalive.sh").read_text()
        self.assertIn("unset ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN", script)
        self.assertIn("sudo -n -u \"$RDC_AGENT_USER\"", script)
        self.assertIn("env -i", script)
        self.assertIn("AGENT_CONTAINER_ENGINE=podman", script)


if __name__ == "__main__":
    unittest.main()
