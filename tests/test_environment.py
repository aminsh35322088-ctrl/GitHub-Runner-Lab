import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class EnvironmentTests(unittest.TestCase):
    def test_manifest_detects_lockfile_change(self):
        import lab_environment as mod
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'package-lock.json').write_text('{"lockfileVersion":3}')
            first = mod.capture(path)
            (path / 'package-lock.json').write_text('{"lockfileVersion":2}')
            second = mod.capture(path)
            self.assertNotEqual(first['lockfiles'], second['lockfiles'])
            self.assertIn('lockfiles', mod.compare(first, second))

    def test_runtime_exposes_safe_work_budget(self):
        from lab_common import runtime
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AGENT_KIT_CACHE_DIR': tmp}):
            (Path(tmp) / 'runtime.env').write_text(
                f'START_EPOCH={int(time.time())}\nHANDOFF_EPOCH={int(time.time())+1800}\nAUTO_HANDOFF_MINUTES=20\n')
            report = runtime()
            self.assertLessEqual(report['safe_work_seconds'], 600)
            self.assertGreater(report['safe_work_seconds'], 590)

    def test_broken_recovery_blocks_readiness(self):
        from lab_ready import persistence_status
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AGENT_KIT_CACHE_DIR': tmp}):
            (Path(tmp) / 'recovery.blocked').touch()
            self.assertFalse(persistence_status()['ok'])

    def test_cache_identity_changes_with_runtime(self):
        from lab_environment import cache_identity
        self.assertNotEqual(cache_identity('v22.0.0', 'Linux', 'x86_64'),
                            cache_identity('v26.0.0', 'Linux', 'x86_64'))

    def test_cli_works_through_installed_symlink(self):
        import json
        import subprocess
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / 'agent-lab'
            alias.symlink_to(scripts / 'agent-run.sh')
            result = subprocess.run([str(alias), 'onboard'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('report_problem', json.loads(result.stdout)['commands'])

    def test_pending_incident_survives_encrypted_recovery(self):
        import json
        import subprocess
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, 'AGENT_KIT_CACHE_DIR': str(root / 'kit'),
                   'AGENT_WORKSPACE_ROOT': str(root / 'workspaces'),
                   'AGENT_CHECKPOINT_DIR': str(root / 'checkpoints'),
                   'AGENT_CHECKPOINT_ARTIFACT_DIR': str(root / 'artifacts'),
                   'AGENT_JOBS_DIR': str(root / 'jobs'),
                   'RDC_STATE_KEY': 'test-only-key-at-least-32-characters'}
            pending = root / 'kit/incidents'
            pending.mkdir(parents=True)
            fingerprint = 'a' * 24
            report = {'schema_version': 1, 'fingerprint': fingerprint, 'body': 'sanitized'}
            (pending / (fingerprint + '.json')).write_text(json.dumps(report))
            commands = [
                ['lab_checkpoint.py', 'save', 'test'],
                ['lab_archive.py', 'package'],
                ['lab_archive.py', 'decrypt', str(root / 'artifacts/latest.enc'), str(root / 'decrypted')],
            ]
            for command in commands:
                result = subprocess.run([sys.executable, str(scripts / command[0]), *command[1:]],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            env['AGENT_KIT_CACHE_DIR'] = str(root / 'fresh-kit')
            result = subprocess.run([sys.executable, str(scripts / 'lab_checkpoint.py'), 'resume',
                                     str(root / 'decrypted/snapshot'), str(root / 'recovered')],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = root / 'fresh-kit/incidents' / (fingerprint + '.json')
            self.assertEqual(json.loads(restored.read_text()), report)


if __name__ == '__main__':
    unittest.main()
