import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import lab_secrets


class SecretInjectionTests(unittest.TestCase):
    def test_secret_is_only_in_child_environment_and_not_saved(self):
        secret = 'test-secret-value-never-log-this'
        seen = {}
        original = os.environ.get('TELEGRAM_BOT_TOKEN')
        def fake_runner(command, *, env, check):
            seen['command'] = command
            seen['value'] = env.get('TELEGRAM_BOT_TOKEN')
            seen['check'] = check
            return SimpleNamespace(returncode=0)
        code = lab_secrets.run('TELEGRAM_BOT_TOKEN', ['trusted-tool', '--flag'],
            prompt_fn=lambda _: secret, terminal_check=lambda: True, runner=fake_runner)
        self.assertEqual(code, 0)
        self.assertEqual(seen['value'], secret)
        self.assertEqual(seen['command'], ['trusted-tool', '--flag'])
        self.assertFalse(seen['check'])
        self.assertEqual(os.environ.get('TELEGRAM_BOT_TOKEN'), original)
        self.assertNotIn(secret, seen['command'])

    def test_refuses_noncredential_names_before_prompt(self):
        called = []
        with self.assertRaisesRegex(ValueError, 'credential-related'):
            lab_secrets.run('PATH', ['true'], prompt_fn=lambda _: called.append(True),
                            terminal_check=lambda: True)
        self.assertEqual(called, [])

    def test_refuses_noninteractive_terminal_before_prompt(self):
        called = []
        with self.assertRaisesRegex(ValueError, 'interactive terminal'):
            lab_secrets.run('TGCLOUD_TOKEN', ['true'], prompt_fn=lambda _: called.append(True),
                            terminal_check=lambda: False)
        self.assertEqual(called, [])

    def test_refuses_empty_secret(self):
        with self.assertRaisesRegex(ValueError, 'Empty credentials'):
            lab_secrets.run('TGCLOUD_TOKEN', ['true'], prompt_fn=lambda _: '',
                            terminal_check=lambda: True)

    def test_propagates_child_exit_code(self):
        code = lab_secrets.run('TGCLOUD_TOKEN', ['python3', '-c', 'raise SystemExit(7)'],
            prompt_fn=lambda _: 'x', terminal_check=lambda: True)
        self.assertEqual(code, 7)

    def test_cli_documents_command(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'lab_secrets.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('without saving it', result.stdout)


if __name__ == '__main__':
    unittest.main()
