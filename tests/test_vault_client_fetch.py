import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch-vault-client.sh"
REF = "a" * 40
CLIENT = "export const ok = true\n"


def fake_bin(directory, *, curl_ok, gh_ok):
    bin_dir = Path(directory) / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\n"
        + ('out=""; while [[ $# -gt 0 ]]; do [[ "$1" == --output ]] && out="$2"; shift; done\n'
           f"printf '%s' '{CLIENT}' > \"$out\"\n" if curl_ok else "exit 22\n")
    )
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env bash\n"
        + (f"echo \"gh-token:$GH_TOKEN\" >> \"$FETCH_LOG\"\nprintf '%s' '{CLIENT}'\n" if gh_ok else "exit 1\n")
    )
    for name in ("curl", "gh"):
        path = bin_dir / name
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def run(curl_ok, gh_ok, token="", ref=REF):
    with tempfile.TemporaryDirectory() as d:
        bin_dir = fake_bin(d, curl_ok=curl_ok, gh_ok=gh_ok)
        dest = Path(d) / "vault.mjs"
        log = Path(d) / "log"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "VAULT_CLIENT_REF": ref,
            "VAULT_CLIENT_GITHUB_TOKEN": token,
            "FETCH_LOG": str(log),
        }
        result = subprocess.run([str(SCRIPT), str(dest)], env=env, capture_output=True, text=True)
        installed = {
            "exists": dest.exists(),
            "text": dest.read_text() if dest.exists() else "",
            "mode": oct(dest.stat().st_mode & 0o777) if dest.exists() else "",
        }
        return result, installed, log.read_text() if log.exists() else ""


class FetchVaultClientTests(unittest.TestCase):
    def test_public_download_needs_no_token(self):
        result, dest, log = run(curl_ok=True, gh_ok=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_CLIENT_SOURCE=public", result.stdout)
        self.assertEqual(dest["text"], CLIENT)
        self.assertEqual(dest["mode"], "0o600")
        self.assertEqual(log, "")

    def test_token_is_only_a_fallback(self):
        result, dest, log = run(curl_ok=False, gh_ok=True, token="fallback-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_CLIENT_SOURCE=token-fallback", result.stdout)
        self.assertEqual(dest["text"], CLIENT)
        self.assertIn("gh-token:fallback-token", log)
        self.assertNotIn("fallback-token", result.stdout + result.stderr)

    def test_no_download_path_degrades_instead_of_succeeding(self):
        result, dest, _ = run(curl_ok=False, gh_ok=False, token="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest["exists"])
        self.assertIn("degraded", result.stdout + result.stderr)

    def test_ref_must_be_an_immutable_commit_sha(self):
        for ref in ("main", "v1", "abc", "A" * 40, ""):
            result, dest, _ = run(curl_ok=True, gh_ok=True, ref=ref)
            self.assertNotEqual(result.returncode, 0, ref)
            self.assertFalse(dest["exists"], ref)


if __name__ == "__main__":
    unittest.main()
