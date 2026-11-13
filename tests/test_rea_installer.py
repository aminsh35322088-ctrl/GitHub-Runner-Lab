"""Behavioral tests for scripts/install-rea.sh (PR #31 review fixes).

Unlike the string-matching contract tests in test_lab.py, these tests run the
installer (or its download_pinned function, extracted verbatim) with shimmed
tools and assert on observable behavior:

- ``--dry-run`` never sources an existing providers.env and creates nothing.
- ``download_pinned`` verifies SHA-256 for cached files too: a corrupt cached
  archive is re-downloaded, and a download whose checksum does not match the
  pin is rejected.
"""
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-rea.sh"


def extract_function(source: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", source, re.M | re.S)
    if not match:
        raise AssertionError(f"function {name} not found in installer")
    return f"{name}() {{\n{match.group(1)}}}"


class DryRunTests(unittest.TestCase):
    def run_dry(self, root: Path) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(root / "home"),
            "REA_INSTALL_ROOT": str(root / "rea"),
        }
        (root / "home").mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["bash", str(INSTALLER), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_dry_run_never_sources_providers_env(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rea = root / "rea"
            rea.mkdir()
            marker = root / "sourced-marker"
            (rea / "providers.env").write_text(
                f"touch {marker}\nexport REA_VERSION='0.0.0-poisoned'\n"
            )
            result = self.run_dry(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                marker.exists(),
                "dry-run sourced providers.env; it must never execute that file",
            )
            self.assertIn("DRY-RUN", result.stdout)

    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.run_dry(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "rea").exists(), "dry-run created the install root")
            created = [p for p in (root / "home").rglob("*") if p.is_file()]
            self.assertEqual(created, [], f"dry-run wrote files under HOME: {created}")


class DownloadPinnedTests(unittest.TestCase):
    def setUp(self):
        self.installer_text = INSTALLER.read_text()
        self.function = extract_function(self.installer_text, "download_pinned")

    def run_download(self, td: Path, fixture: bytes, expected_sha: str,
                     preseed: bytes | None) -> subprocess.CompletedProcess:
        bindir = td / "bin"
        bindir.mkdir()
        served = td / "served.bin"
        served.write_bytes(fixture)
        # curl shim: ignore URL/flags, copy the fixture to the --output target.
        (bindir / "curl").write_text(
            "#!/usr/bin/env bash\n"
            "out=''\n"
            "while (($#)); do case \"$1\" in --output) out=\"$2\"; shift 2;; *) shift;; esac; done\n"
            f"cp \"{served}\" \"$out\"\n"
        )
        (bindir / "curl").chmod(0o755)
        dest = td / "dest" / "provider.jar"
        if preseed is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(preseed)
        harness = td / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
            "log() { printf '%s\\n' \"$*\"; }\n"
            "die() { printf 'ERROR: %s\\n' \"$*\" >&2; exit 1; }\n"
            f"{self.function}\n"
            f"download_pinned 'https://example.invalid/provider.jar' '{expected_sha}' \"{dest}\"\n"
        )
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(td)}
        result = subprocess.run(["bash", str(harness)], capture_output=True,
                                text=True, env=env, timeout=60)
        return result

    def test_matching_download_is_accepted(self):
        fixture = b"pinned-provider-bytes"
        sha = hashlib.sha256(fixture).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            result = self.run_download(Path(td), fixture, sha, preseed=None)
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = Path(td) / "dest" / "provider.jar"
            self.assertEqual(dest.read_bytes(), fixture)

    def test_checksum_mismatch_is_rejected(self):
        fixture = b"attacker-controlled-bytes"
        wrong_sha = hashlib.sha256(b"the-real-provider").hexdigest()
        with tempfile.TemporaryDirectory() as td:
            result = self.run_download(Path(td), fixture, wrong_sha, preseed=None)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 mismatch", result.stderr)

    def test_corrupt_cached_file_is_replaced_not_trusted(self):
        """Regression test for the JADX/Apktool bypass: a pre-existing file at
        the destination must be re-verified, never trusted for existing."""
        fixture = b"verified-provider-bytes"
        sha = hashlib.sha256(fixture).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            result = self.run_download(Path(td), fixture, sha, preseed=b"corrupt-cache")
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = Path(td) / "dest" / "provider.jar"
            self.assertEqual(dest.read_bytes(), fixture)


class RunnerShellIntegrationTests(unittest.TestCase):
    def test_binwalk_cli_is_linked_into_shared_provider_bin(self):
        installer = INSTALLER.read_text()
        function = extract_function(installer, "link_binwalk_cli")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider_bin = root / "rea" / "binwalk" / "bin"
            shared_bin = root / "runner-tools" / "bin"
            provider_bin.mkdir(parents=True)
            binary = provider_bin / "binwalk"
            binary.write_text("#!/usr/bin/env bash\\necho 'binwalk 3.1.0'\\n")
            binary.chmod(0o755)
            harness = root / "link-binwalk.sh"
            harness.write_text(
                "#!/usr/bin/env bash\\nset -Eeuo pipefail\\n"
                "log() { :; }\\n"
                "die() { echo \\"$*\\" >&2; exit 1; }\\n"
                f"{function}\\n"
                f"link_binwalk_cli \\"{binary}\\" \\"{shared_bin}\\" 3.1.0\\n"
                f"PATH=\\"{shared_bin}:$PATH\\"\\nexport PATH\\n"
                "test \\"$(command -v binwalk)\\" = \\"$shared_bin/binwalk\\"\\n"
                "binwalk --version | grep -q 'binwalk 3.1.0'\\n"
            )
            result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((shared_bin / "binwalk").is_symlink())
            self.assertEqual((shared_bin / "binwalk").resolve(), binary.resolve())

    def test_provider_environment_precedes_bashrc_return_and_covers_bash_profile(self):
        installer = INSTALLER.read_text()
        function = extract_function(installer, "install_provider_shell_environment")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            bin_dir = root / "provider-bin"
            home.mkdir()
            bin_dir.mkdir()
            fake_rea = bin_dir / "rea"
            fake_rea.write_text("#!/usr/bin/env bash\\necho rea-shell-smoke\\n")
            fake_rea.chmod(0o755)
            env_file = root / "providers.env"
            env_file.write_text(
                f"export PATH={bin_dir}:\\\"$PATH\\\"\\n"
                "export REA_TEST_MARKER=loaded\\n"
            )
            bashrc = home / ".bashrc"
            bashrc.write_text(
                "# stock bashrc\\n"
                "case $- in\\n"
                "  *i*) ;;\\n"
                "  *) return;;\\n"
                "esac\\n"
                "export BASHRC_BODY_REACHED=1\\n"
            )
            profile = home / ".profile"
            profile.write_text("# profile body\\n")
            bash_profile = home / ".bash_profile"
            bash_profile.write_text("export EXISTING_LOGIN_SETUP=preserved\\n")
            harness = root / "install-provider-env.sh"
            harness.write_text(
                "#!/usr/bin/env bash\\nset -Eeuo pipefail\\n"
                f"{function}\\n"
                f"install_provider_shell_environment \\"{env_file}\\"\\n"
                f"install_provider_shell_environment \\"{env_file}\\"\\n"
            )
            run_env = os.environ.copy()
            run_env["HOME"] = str(home)
            run_env.pop("REA_PROVIDERS_ENV_LOADED", None)
            result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, env=run_env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

            start = "# >>> GitHub Runner Lab REA environment >>>"
            end = "# <<< GitHub Runner Lab REA environment <<<"
            for startup in (profile, bashrc, bash_profile):
                text = startup.read_text()
                self.assertEqual(text.count(start), 1, startup.name)
                self.assertEqual(text.count(end), 1, startup.name)
                self.assertTrue(text.startswith(start), startup.name)
            self.assertLess(bashrc.read_text().index(start), bashrc.read_text().index("*) return;;"))
            self.assertIn("EXISTING_LOGIN_SETUP=preserved", bash_profile.read_text())

            for startup in (bashrc, bash_profile, profile):
                shell = [
                    "bash", "--noprofile", "--norc", "-c",
                    f"source \\"{startup}\\"; test \\"$REA_TEST_MARKER\\" = loaded; command -v rea",
                ]
                checked = subprocess.run(shell, capture_output=True, text=True, env=run_env, timeout=30)
                self.assertEqual(checked.returncode, 0, f"{startup.name}: {checked.stderr}")
                self.assertEqual(checked.stdout.strip(), str(fake_rea))
class ResolutionContractTests(unittest.TestCase):
    def test_rea_resolution_is_single_exact_and_verified(self):
        text = INSTALLER.read_text()
        # One floating lookup (the dist-tag -> version resolution); everything
        # else is addressed by the immutable exact spec.
        self.assertEqual(text.count('npm view "$REA_PACKAGE_NAME@$REA_DIST_TAG"'), 1)
        self.assertIn('npm view "$REA_SPEC" engines.node', text)
        self.assertIn('npm view "$REA_SPEC" dist.tarball', text)
        self.assertIn('npm view "$REA_SPEC" dist.integrity', text)
        # The install consumes the verified local tarball, not a floating spec.
        self.assertIn('npm install --global --allow-scripts=frida "$REA_TARBALL"', text)
        self.assertNotIn('"$REA_PACKAGE"', text)


if __name__ == "__main__":
    unittest.main()
