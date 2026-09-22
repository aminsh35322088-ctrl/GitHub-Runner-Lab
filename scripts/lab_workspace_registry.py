#!/usr/bin/env python3
"""Explicit registry for agent workspaces outside the managed workspace root."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from lab_common import atomic, git, kit, lock, path_env, run, workspace_root


def registry_file():
    return path_env('AGENT_WORKSPACE_REGISTRY', kit() / 'workspace-registry.json')


def _default_state():
    return {'version': 1, 'workspaces': []}


def load_state():
    path = registry_file()
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f'Workspace registry is unreadable: {exc}')
    if raw.get('version') != 1 or not isinstance(raw.get('workspaces'), list):
        raise RuntimeError('Workspace registry has an unsupported schema')
    return raw


def _repository_root(path):
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise RuntimeError(f'Workspace does not exist: {candidate}')
    result = run(['git', '-C', candidate, 'rev-parse', '--show-toplevel'], check=False)
    if result.returncode:
        raise RuntimeError(f'Workspace is not a Git repository: {candidate}')
    top = Path(result.stdout.decode().strip()).resolve()
    if not top.exists() or not top.is_dir():
        raise RuntimeError(f'Git top-level is unavailable: {top}')
    return top


def recovery_name(path):
    path = Path(path).resolve()
    base = re.sub(r'[^A-Za-z0-9_.-]+', '-', path.name).strip('-') or 'workspace'
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:10]
    return f'{base}-{digest}'


def adopt(path, quiet=False):
    repo = _repository_root(path)
    root = workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    reg = registry_file()
    reg.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(reg.with_suffix('.lock')):
        state = load_state()
        current = {str(Path(item['path']).resolve()): item
                   for item in state['workspaces']
                   if isinstance(item, dict) and item.get('path')}
        key = str(repo)
        item = current.get(key, {})
        item.update({
            'path': key,
            'name': recovery_name(repo),
            'adopted_at': item.get('adopted_at', time.time()),
        })
        current[key] = item
        state['workspaces'] = sorted(current.values(), key=lambda x: x['path'])
        atomic(reg, state)
    if not quiet:
        print(f'WORKSPACE_ADOPTED={repo}')
        print(f'WORKSPACE_RECOVERY_NAME={item["name"]}')
    return repo


def forget(path):
    repo = _repository_root(path)
    reg = registry_file()
    with lock(reg.with_suffix('.lock')):
        state = load_state()
        before = len(state['workspaces'])
        state['workspaces'] = [
            item for item in state['workspaces']
            if str(Path(item.get('path', '')).expanduser().resolve()) != str(repo)
        ]
        atomic(reg, state)
    print(f'WORKSPACE_FORGOTTEN={repo}')
    print(f'WORKSPACE_REMOVED={before - len(state["workspaces"])}')


def registered_repositories():
    state = load_state()
    out = []
    seen = set()
    for item in state['workspaces']:
        if not isinstance(item, dict) or not item.get('path'):
            continue
        try:
            repo = _repository_root(item['path'])
        except RuntimeError:
            continue
        key = str(repo)
        if key in seen:
            continue
        seen.add(key)
        out.append((repo, item.get('name') or recovery_name(repo)))
    return out


def list_registered():
    for repo, name in registered_repositories():
        print(f'{name}\t{repo}')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    adopt_parser = sub.add_parser('adopt')
    adopt_parser.add_argument('path')
    forget_parser = sub.add_parser('forget')
    forget_parser.add_argument('path')
    sub.add_parser('list')
    args = parser.parse_args()

    if args.action == 'adopt':
        adopt(args.path)
    elif args.action == 'forget':
        forget(args.path)
    else:
        list_registered()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Workspace registry failed: {exc}', file=sys.stderr)
        sys.exit(2)
