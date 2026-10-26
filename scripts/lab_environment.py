#!/usr/bin/env python3
"""Secret-free environment identity and agent onboarding; no dependency installation."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys

from lab_common import SCRIPTS, atomic, kit, runtime

LOCKFILES = ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock',
             'bun.lock', 'bun.lockb', 'uv.lock', 'poetry.lock', 'requirements.txt',
             'Cargo.lock', 'go.sum', '.github/agent-lab/contract.json',
             '.github/agent-lab/runner.sh')
TOOLS = {'node': ['node', '--version'], 'npm': ['npm', '--version'],
         'python': ['python3', '--version'], 'git': ['git', '--version'],
         'gh': ['gh', '--version'], 'go': ['go', 'version'],
         'rust': ['rustc', '--version'], 'cmake': ['cmake', '--version']}


def output(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return result.stdout.splitlines()[0][:300] if result.returncode == 0 and result.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


def cache_identity(node, system, machine):
    raw = json.dumps([system, machine, node], separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def capture(workspace=None):
    report = {'schema_version': 1, 'system': platform.system(),
              'architecture': platform.machine(),
              'os_release': platform.freedesktop_os_release().get('VERSION_ID', '')
              if hasattr(platform, 'freedesktop_os_release') else '',
              'tools': {name: output(command) for name, command in TOOLS.items()},
              'lab_sha': output(['git', '-C', str(SCRIPTS.parent), 'rev-parse', 'HEAD']),
              'toolset_sha256': hashlib.sha256(
                  (SCRIPTS.parent / 'config/runner-toolset.json').read_bytes()).hexdigest(),
              'lockfiles': {}}
    if workspace:
        workspace = Path(workspace).resolve()
        if not workspace.is_dir():
            raise ValueError('Workspace must exist')
        report['project_sha'] = output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'])
        for name in LOCKFILES:
            file = workspace / name
            if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(workspace):
                report['lockfiles'][name] = hashlib.sha256(file.read_bytes()).hexdigest()
    return report


def compare(expected, actual):
    return [key for key in sorted(set(expected) | set(actual)) if expected.get(key) != actual.get(key)]


def onboarding():
    from lab_ready import persistence_status
    return {'schema_version': 1, 'runtime': runtime(), 'persistence': persistence_status(),
            'instructions': str(SCRIPTS.parent / 'AGENTS.md'),
            'commands': {
                'status': 'agent-lab status', 'preflight': 'agent-lab ready --workspace PATH',
                'prepare': 'agent-lab work --repo URL --ref BRANCH',
                'checkpoint': 'agent-lab checkpoint manual',
                'environment': 'agent-lab environment --workspace PATH --output FILE',
                'reproduce_check': 'agent-lab environment --workspace PATH --compare FILE',
                'report_problem': 'agent-lab issue --help', 'retry_reports': 'agent-lab issue --flush',
                'capabilities': 'agent-lab shell-help',
                'secret_injection': 'agent-lab secret run NAME -- COMMAND (one-shot; never saved)'},
            'rules': ['Read Lab and target repository instructions before work.',
                      'Check safe_work_seconds before long operations; checkpoint before the restart window.',
                      'Push durable source to GitHub; dependency caches may be evicted.',
                      'After recovery run the target project prepare hook to rebuild dependencies.',
                      'Report reproduced Lab defects using issue; never publish credentials or project failures.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--onboard', action='store_true')
    parser.add_argument('--cache-key', action='store_true')
    args = parser.parse_args()
    if args.cache_key:
        print(cache_identity(output(TOOLS['node']), platform.system(), platform.machine()))
        return 0
    report = onboarding() if args.onboard else capture(args.workspace)
    if args.output:
        atomic(args.output, report)
    if args.compare:
        differences = compare(json.loads(args.compare.read_text()), report)
        print(json.dumps({'matches': not differences, 'differences': differences}, indent=2))
        return 1 if differences else 0
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'Environment report failed: {error}', file=sys.stderr)
        raise SystemExit(2)
