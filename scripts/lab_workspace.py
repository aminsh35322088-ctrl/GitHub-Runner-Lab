#!/usr/bin/env python3
"""Prepare a workspace with fast-forward-only updates, never reset local work."""
import argparse
import hashlib
from pathlib import Path
import os
import re
import sys
from lab_common import git, lock, run, runtime, workspace_root


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', default='https://github.com/aminsh35322088-ctrl/opencode-telegram-bot.git')
    p.add_argument('--ref', default='main'); p.add_argument('--pr', type=int)
    p.add_argument('--dir'); p.add_argument('--deps', action='store_true')
    p.add_argument('--force', action='store_true', help='Deprecated; never discards work')
    a = p.parse_args()
    if runtime()['state'] not in ('SAFE', 'UNKNOWN'):
        raise RuntimeError('Workspace preparation refused during handoff')
    if a.pr is not None and a.pr < 1: raise ValueError('Invalid PR')
    if a.repo.startswith('-') or a.ref.startswith('-'): raise ValueError('Invalid repository/ref')
    if re.search(r'https?://[^/]*@', a.repo): raise ValueError('Credentials must not be embedded in repository URLs')
    label = 'pr-' + str(a.pr) if a.pr else re.sub(r'[^A-Za-z0-9_.-]', '-', a.ref)
    key = hashlib.sha256(a.repo.encode()).hexdigest()[:10]
    name = Path(a.repo.removesuffix('.git')).name
    root = workspace_root(); root.mkdir(parents=True, exist_ok=True)
    dest = Path(a.dir).resolve() if a.dir else root / f'{name}-{key}-{label}'
    with lock(root / '.locks' / (hashlib.sha256(str(dest).encode()).hexdigest() + '.lock')):
        if not (dest / '.git').exists():
            if dest.exists(): raise RuntimeError('Destination exists and is not a Git workspace')
            run(['git', 'clone', '--no-tags', '--', a.repo, dest])
        if git(dest, 'remote', 'get-url', 'origin') != a.repo:
            raise RuntimeError('Workspace belongs to a different remote')
        if git(dest, 'status', '--porcelain'):
            raise RuntimeError('Dirty workspace preserved; commit/checkpoint or use another --dir')
        git(dest, 'fetch', '--prune', 'origin')
        if a.pr:
            target = f'refs/remotes/origin/pr/{a.pr}'
            git(dest, 'fetch', 'origin', f'+refs/pull/{a.pr}/head:{target}')
            branch = f'pr-{a.pr}'
        else:
            target = f'refs/remotes/origin/{a.ref}'; branch = a.ref
            if run(['git', '-C', dest, 'show-ref', '--verify', target], check=False).returncode:
                git(dest, 'fetch', 'origin', a.ref); target = 'FETCH_HEAD'; branch = None
        # A clean tree can still contain valuable unpublished commits.
        if run(['git', '-C', dest, 'merge-base', '--is-ancestor', 'HEAD', target], check=False).returncode:
            raise RuntimeError('Local/divergent commits preserved; create a separate workspace')
        if branch:
            exists = run(['git', '-C', dest, 'show-ref', '--verify', f'refs/heads/{branch}'], check=False).returncode == 0
            if exists:
                if run(['git', '-C', dest, 'merge-base', '--is-ancestor', branch, target], check=False).returncode:
                    raise RuntimeError('Destination branch contains local/divergent commits')
                git(dest, 'checkout', branch); git(dest, 'merge', '--ff-only', target)
            else: git(dest, 'checkout', '-b', branch, target)
        else: git(dest, 'checkout', '--detach', target)
        if a.deps:
            raise RuntimeError('Use a managed project job for dependency installation; workspace is ready')
    print(f'AGENT_WORKSPACE={dest}')
    print(f'AGENT_SHA={git(dest, "rev-parse", "HEAD")}')
    print(f'AGENT_BRANCH={git(dest, "branch", "--show-current")}')


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'Workspace refused: {e}', file=sys.stderr); sys.exit(3)
