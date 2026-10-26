#!/usr/bin/env python3
"""Inject one credential into one trusted foreground command without persisting it."""
import argparse
import getpass
import os
import re
import subprocess
import sys

CREDENTIAL_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,127}$')
CREDENTIAL_MARKER = re.compile(r'(^|_)(TOKEN|SECRET|PASSWORD|PASS|KEY|CREDENTIAL|AUTH)(_|$)')


def run(name, command, *, prompt_fn=None, terminal_check=None, runner=None):
    prompt_fn = prompt_fn or getpass.getpass
    terminal_check = terminal_check or (lambda: sys.stdin.isatty() and sys.stderr.isatty())
    runner = runner or subprocess.run

    if not CREDENTIAL_NAME.fullmatch(name) or not CREDENTIAL_MARKER.search(name):
        raise ValueError('Variable name must be uppercase and clearly credential-related (for example, TELEGRAM_BOT_TOKEN).')
    if not command:
        raise ValueError('Provide a trusted foreground command after --.')
    if not terminal_check():
        raise ValueError('Secret injection requires an interactive terminal; no value was read or stored.')

    value = prompt_fn(f'Value for {name} (input hidden; not saved): ')
    if not value:
        raise ValueError('Empty credentials are refused.')

    child_env = os.environ.copy()
    child_env[name] = value
    # Avoid retaining another reference after the child exits. The secret is never
    # written to disk, included in argv, or passed to managed/untrusted jobs.
    value = None
    try:
        completed = runner(command, env=child_env, check=False)
        return int(completed.returncode)
    finally:
        child_env.pop(name, None)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-lab secret',
        description='Inject a credential into one trusted foreground command without saving it.')
    sub = parser.add_subparsers(dest='action', required=True)
    p_run = sub.add_parser('run', help='prompt for one secret and expose it only to a foreground command')
    p_run.add_argument('name', help='environment variable name, e.g. TGCLOUD_TOKEN')
    p_run.add_argument('command', nargs=argparse.REMAINDER,
                       help='trusted command to run; separate it from NAME with --')
    args = parser.parse_args(argv)
    if args.action == 'run':
        command = list(args.command)
        if command and command[0] == '--':
            command.pop(0)
        try:
            return run(args.name, command)
        except (EOFError, ValueError, OSError) as exc:
            print(f'agent-lab secret: {exc}', file=sys.stderr)
            return 2
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
