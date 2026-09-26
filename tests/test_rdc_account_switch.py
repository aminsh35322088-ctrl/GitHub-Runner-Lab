import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "prepare-rdc-account-switch.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "rdc-lab.yml"


class RdcAccountSwitchTest(unittest.TestCase):
    def test_account_switch_owns_runner_concurrency_group(self):
        text = WORKFLOW.read_text()
        section = text.split("  account-switch:", 1)[1].split("  runner-lab:", 1)[0]
        self.assertIn("group: rdc-runner-lab", section)
        self.assertIn("cancel-in-progress: true", section)

    def test_cancel_is_sent_once_then_escalates_once(self):
        with tempfile.TemporaryDirectory(prefix="rdc-switch-test-") as temp:
            temp = Path(temp)
            bindir = temp / "bin"
            bindir.mkdir()
            state = temp / "state"
            state.write_text("0")
            log = temp / "requests.log"

            curl = bindir / "curl"
            curl.write_text("""#!/usr/bin/env bash
set -eu
url="${!#}"
is_post=false
out_file=""
previous=""
for arg in "$@"; do
  if [[ "$previous" == "-o" ]]; then out_file="$arg"; fi
  if [[ "$arg" == "-X" ]]; then is_post=true; fi
  previous="$arg"
done

if [[ "$is_post" == "true" ]]; then
  printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
  [[ -z "$out_file" ]] || printf '{}\\n' > "$out_file"
  printf '202'
  exit 0
fi

poll="$(cat "$FAKE_CURL_STATE")"
poll=$((poll + 1))
printf '%s\\n' "$poll" > "$FAKE_CURL_STATE"
if (( poll <= 2 )); then
  printf '%s\\n' '{"workflow_runs":[{"id":42,"status":"in_progress"}]}'
else
  printf '%s\\n' '{"workflow_runs":[]}'
fi
""")
            curl.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "PATH": str(bindir) + os.pathsep + env["PATH"],
                "GH_TOKEN": "test-token",
                "REPO": "example/repo",
                "SELF_RUN_ID": "99",
                "WORKFLOW": "rdc-lab.yml",
                "FAKE_CURL_STATE": str(state),
                "FAKE_CURL_LOG": str(log),
                "RDC_SWITCH_POLL_SECONDS": "0",
                "RDC_SWITCH_FORCE_AFTER_SECONDS": "0",
                "RDC_SWITCH_MAX_POLLS": "6",
            })
            result = subprocess.run(
                ["bash", str(HELPER)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            requests = log.read_text().splitlines()
            self.assertEqual(
                requests,
                [
                    "https://api.github.com/repos/example/repo/actions/runs/42/cancel",
                    "https://api.github.com/repos/example/repo/actions/runs/42/force-cancel",
                ],
            )
            lines = result.stdout.splitlines()
            self.assertEqual(sum(": cancellation accepted (HTTP 202)." in line for line in lines), 1)
            self.assertEqual(sum(": force-cancellation accepted (HTTP 202)." in line for line in lines), 1)


if __name__ == "__main__":
    unittest.main()
