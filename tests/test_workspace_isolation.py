import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(args, *, env, cwd=ROOT, timeout=20):
    merged = os.environ.copy()
    merged.update({k: str(v) for k, v in env.items()})
    return subprocess.run([str(x) for x in args], cwd=cwd, env=merged,
                          capture_output=True, text=True, timeout=timeout)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def init_repo(path, remote=None):
    subprocess.run(["git", "init", "--initial-branch=main", str(path)],
                   check=True, capture_output=True)
    git(path, "config", "user.name", "Lab Test")
    git(path, "config", "user.email", "lab@example.invalid")
    (path / "tracked.txt").write_text("base\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    if remote:
        git(path, "remote", "add", "origin", str(remote))
        git(path, "push", "-u", "origin", "main")


class WorkspaceIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-isolation-")
        self.base = Path(self.temp.name)
        self.env = {
            "AGENT_WORKSPACE_ROOT": self.base / "workspaces",
            "AGENT_JOBS_DIR": self.base / "jobs",
            "AGENT_KIT_CACHE_DIR": self.base / "kit",
        }
        Path(self.env["AGENT_WORKSPACE_ROOT"]).mkdir()
        Path(self.env["AGENT_KIT_CACHE_DIR"]).mkdir()
        now = int(time.time())
        (Path(self.env["AGENT_KIT_CACHE_DIR"]) / "runtime.env").write_text(
            f"START_EPOCH={now}\nHANDOFF_EPOCH={now + 3600}\nAUTO_HANDOFF_MINUTES=20\n"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_prepare_waits_for_active_managed_job(self):
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", remote],
                       check=True, capture_output=True)
        seed = self.base / "seed"
        init_repo(seed, remote)
        workspace = self.base / "workspaces" / "demo"
        prepare = [SCRIPTS / "agent-workspace.sh", "--repo", remote, "--dir", workspace]
        first = run(prepare, env=self.env)
        self.assertEqual(first.returncode, 0, first.stderr)

        marker = self.base / "job-started"
        code = (
            "from pathlib import Path; import time; "
            f"Path({str(marker)!r}).write_text('ready'); time.sleep(1.5)"
        )
        started = run(
            [sys.executable, SCRIPTS / "lab_jobs.py", "start", "--cwd", workspace,
             "--timeout", "10", "--", sys.executable, "-c", code],
            env=self.env,
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        job = started.stdout.strip()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "managed job never reached the locked execution phase")

        before = time.monotonic()
        second = run(prepare, env=self.env, timeout=10)
        elapsed = time.monotonic() - before
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertGreaterEqual(elapsed, 1.0)

        waited = run([sys.executable, SCRIPTS / "lab_jobs.py", "wait", job], env=self.env)
        self.assertEqual(waited.returncode, 0, waited.stderr)

    def test_validation_fails_stage_before_cleanup_when_detached_child_survives(self):
        workspace = self.base / "workspaces" / "detached"
        init_repo(workspace)
        hook = workspace / ".github/agent-lab/runner.sh"
        hook.parent.mkdir(parents=True)
        hook.write_text("""#!/usr/bin/env bash
set -eu
case "$1" in
  full) sleep 30 >/dev/null 2>&1 & exit 0 ;;
  clean-materialized) touch "$AGENT_PROJECT_ROOT/cleanup-ran" ;;
esac
""")
        hook.chmod(0o755)
        output = self.base / "validation"
        env = {**self.env, "AGENT_PROJECT_ROOT": workspace}
        result = run(
            [sys.executable, SCRIPTS / "lab_validate.py", workspace, "--output-dir", output],
            env=env,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        summary = json.loads((output / "summary.json").read_text())
        full = next(stage for stage in summary["stages"] if stage["name"] == "full")
        self.assertEqual(full["exit_code"], 125)
        self.assertGreaterEqual(full["stray_processes_terminated"], 1)
        self.assertTrue((workspace / "cleanup-ran").exists())


if __name__ == "__main__":
    unittest.main()
