import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch-vault-client.sh"
REF = "a" * 40
REPOSITORY = "example/runner-vault"
CLIENT = "export const ok = true\n"


def fake_bin(directory, *, public_download):
    bin_dir = Path(directory) / "bin"
    bin_dir.mkdir()
    curl_script = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\0' "$@" >> "$FETCH_CURL_ARGS_LOG"
out=""
has_config=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) out="$2"; shift 2; continue ;;
    --config) [[ "$2" == "-" ]] && has_config=true; shift 2; continue ;;
  esac
  shift
done
if [[ "$has_config" != true ]]; then
  if [[ "${FETCH_PUBLIC_DOWNLOAD:-}" == "true" ]]; then
    printf '%b' 'export const ok = true\\n' > "$out"
    exit 0
  fi
  exit 22
fi
cat > "$FETCH_CONFIG_LOG"
printf '%b' 'export const ok = true\\n' > "$out"
"""
    timeout_script = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\0' "$@" > "$FETCH_TIMEOUT_ARGS_LOG"
[[ "$1" == "--signal=TERM" ]] || exit 91
shift
[[ "$1" == "--kill-after=5s" ]] || exit 92
shift
[[ "$1" == "20s" ]] || exit 93
shift
exec "$@"
"""
    (bin_dir / "curl").write_text(curl_script)
    (bin_dir / "timeout").write_text(timeout_script)
    for name in ("curl", "timeout"):
        path = bin_dir / name
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def run(public_download, token="", ref=REF, repository=REPOSITORY):
    with tempfile.TemporaryDirectory() as d:
        bin_dir = fake_bin(d, public_download=public_download)
        dest = Path(d) / "vault.mjs"
        config_log = Path(d) / "curl-config"
        curl_args_log = Path(d) / "curl-args"
        timeout_args_log = Path(d) / "timeout-args"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "VAULT_CLIENT_REPOSITORY": repository,
            "VAULT_CLIENT_REF": ref,
            "VAULT_CLIENT_GITHUB_TOKEN": token,
            "FETCH_PUBLIC_DOWNLOAD": "true" if public_download else "false",
            "FETCH_CONFIG_LOG": str(config_log),
            "FETCH_CURL_ARGS_LOG": str(curl_args_log),
            "FETCH_TIMEOUT_ARGS_LOG": str(timeout_args_log),
        }
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        result = subprocess.run([str(SCRIPT), str(dest)], env=env, capture_output=True, text=True)
        installed = {
            "exists": dest.exists(),
            "text": dest.read_text() if dest.exists() else "",
            "mode": oct(dest.stat().st_mode & 0o777) if dest.exists() else "",
        }
        config = config_log.read_text() if config_log.exists() else ""
        curl_args = curl_args_log.read_bytes() if curl_args_log.exists() else b""
        timeout_args = timeout_args_log.read_bytes() if timeout_args_log.exists() else b""
        return result, installed, config, curl_args, timeout_args


class FetchVaultClientTests(unittest.TestCase):
    def test_public_download_needs_no_token(self):
        result, dest, config, curl_args, timeout_args = run(public_download=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_CLIENT_SOURCE=public", result.stdout)
        self.assertEqual(dest["text"], CLIENT)
        self.assertEqual(dest["mode"], "0o600")
        self.assertEqual(config, "")
        self.assertEqual(timeout_args, b"")
        self.assertIn(f"https://api.github.com/repos/{REPOSITORY}/contents/client/vault.mjs?ref={REF}".encode(), curl_args)

    def test_token_fallback_uses_stdin_config_not_process_arguments(self):
        token = "fallback-token_123"
        result, dest, config, curl_args, timeout_args = run(public_download=False, token=token)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULT_CLIENT_SOURCE=token-fallback", result.stdout)
        self.assertEqual(dest["text"], CLIENT)
        self.assertEqual(config, f'header = "Authorization: Bearer {token}"\n')
        self.assertNotIn(token.encode(), curl_args)
        self.assertNotIn(token.encode(), timeout_args)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertIn(b"--config", curl_args)
        self.assertIn(b"-", curl_args)
        self.assertIn(b"20s", timeout_args)
        self.assertIn(f"https://api.github.com/repos/{REPOSITORY}/contents/client/vault.mjs?ref={REF}".encode(), curl_args)
        self.assertNotIn(b"GH_TOKEN=", timeout_args + curl_args)

    def test_no_download_path_degrades_instead_of_succeeding(self):
        result, dest, config, _, timeout_args = run(public_download=False, token="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest["exists"])
        self.assertEqual(config, "")
        self.assertEqual(timeout_args, b"")
        self.assertIn("degraded", result.stdout + result.stderr)

    def test_repository_must_be_an_owner_name_pair(self):
        for repository in ("", "https://github.com/example/repo", "../repo", "./repo", "owner/../repo", "owner/..", "owner/repo/extra"):
            result, dest, _, _, _ = run(public_download=True, repository=repository)
            self.assertNotEqual(result.returncode, 0, repository)
            self.assertFalse(dest["exists"], repository)

    def test_ref_must_be_an_immutable_commit_sha(self):
        for ref in ("main", "v1", "abc", "A" * 40, ""):
            result, dest, _, _, _ = run(public_download=True, ref=ref)
            self.assertNotEqual(result.returncode, 0, ref)
            self.assertFalse(dest["exists"], ref)

    def test_token_with_line_breaks_is_rejected_before_config_stream(self):
        result, dest, config, _, timeout_args = run(public_download=False, token="token" + chr(10) + 'header = "X-Injected: yes"')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest["exists"])
        self.assertEqual(config, "")
        self.assertEqual(timeout_args, b"")
        self.assertIn("invalid format", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
