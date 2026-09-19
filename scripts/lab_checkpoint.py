#!/usr/bin/env python3
"""Recoverable snapshots. Never overwrites an existing recovery workspace."""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import time
import uuid

from lab_common import atomic, git, lock, path_env, run, workspace_root

MAX_FILE = 2 * 1024 * 1024
SOURCE_SUFFIXES = {'.py', '.sh', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.jsx',
                   '.rs', '.go', '.c', '.h', '.cpp', '.css', '.html', '.md', '.txt',
                   '.json', '.yml', '.yaml', '.toml', '.sql', '.xml'}
SECRET = re.compile(rb'-----BEGIN .*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]+|\b\d{8,12}:[A-Za-z0-9_-]{30,}|\bsk-[A-Za-z0-9_-]{20,}')


def checkpoint_root():
    return path_env('AGENT_CHECKPOINT_DIR', Path.home() / 'agent-checkpoints')


def repositories():
    root = workspace_root()
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                  and (p / '.git').exists())


def safe_untracked(repo, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        return False
    file = repo / relative
    if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(repo.resolve()):
        return False
    if any(x.startswith('.') and x != '.github' for x in relative.parts):
        return False
    if any(re.search(r'(secret|credential|token|password|device\.json|id_rsa)', x, re.I) for x in relative.parts):
        return False
    if file.stat().st_size > MAX_FILE or file.suffix not in SOURCE_SUFFIXES:
        return False
    data = file.read_bytes()
    return b'\0' not in data and not SECRET.search(data)


def snapshot(reason):
    root = checkpoint_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root / '.lock'):
        name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        out = root / name
        out.mkdir(mode=0o700)
        manifest = {'version': 2, 'created': time.time(), 'reason': reason, 'repositories': []}
        try:
            for repo in repositories():
                head = git(repo, 'rev-parse', 'HEAD')
                if git(repo, 'ls-files', '-u'):
                    raise RuntimeError(f'Unmerged index in {repo}; resolve or save it explicitly first')
                dest = out / repo.name
                dest.mkdir(mode=0o700)
                # Full bundle makes recovery independent of origin availability or upstream configuration.
                run(['git', '-C', repo, 'bundle', 'create', dest / 'repository.bundle', '--all', 'HEAD'])
                for filename, args in [('index.patch', ['diff', '--binary', '--cached', 'HEAD']),
                                       ('worktree.patch', ['diff', '--binary'])]:
                    (dest / filename).write_bytes(run(['git', '-C', repo, *args]).stdout)
                included, excluded = [], []
                names = run(['git', '-C', repo, 'ls-files', '--others', '--exclude-standard', '-z']).stdout
                for raw in names.split(b'\0'):
                    if not raw:
                        continue
                    name = os.fsdecode(raw)
                    if safe_untracked(repo, name):
                        target = dest / 'untracked' / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(repo / name, target)
                        included.append(name)
                    else:
                        excluded.append(name)
                # Reject inconsistent Git state. Files being edited during a periodic snapshot
                # are still best-effort; the final snapshot follows managed-job drain.
                if git(repo, 'rev-parse', 'HEAD') != head:
                    raise RuntimeError(f'HEAD moved during checkpoint: {repo}')
                remote = git(repo, 'remote', 'get-url', 'origin', check=False)
                if re.search(r'https?://[^/]*@', remote):
                    remote = ''
                manifest['repositories'].append({'name': repo.name, 'head': head,
                    'branch': git(repo, 'branch', '--show-current'), 'remote': remote,
                    'included_untracked': included, 'excluded_untracked': excluded})
            jobs = path_env('AGENT_JOBS_DIR', Path.home() / 'agent-jobs')
            if jobs.exists():
                shutil.copytree(jobs, out / '_jobs', ignore=shutil.ignore_patterns('home', 'tmp', '*.lock'))
            atomic(out / 'manifest.json', manifest)
            hashes = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in out.rglob('*') if p.is_file()}
            atomic(out / 'sha256.json', hashes)
            link = root / ('.latest-' + uuid.uuid4().hex)
            link.symlink_to(out.name)
            link.replace(root / 'latest')
            print(f'CHECKPOINT_DIR={out}')
            print(f'CHECKPOINT_REPOSITORIES={len(manifest["repositories"])}')
            print(f'CHECKPOINT_EXCLUDED_FILES={sum(len(r["excluded_untracked"]) for r in manifest["repositories"])}')
            return out
        except Exception:
            shutil.rmtree(out)
            raise


def verify(source):
    for name, digest in json.loads((source / 'sha256.json').read_text()).items():
        p = source / name
        if not p.resolve().is_relative_to(source.resolve()) or not p.is_file():
            raise RuntimeError('Invalid snapshot path')
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Checkpoint checksum mismatch: {name}')


def resume(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    verify(source)
    manifest = json.loads((source / 'manifest.json').read_text())
    if destination.exists():
        raise RuntimeError('Recovery destination must not exist; existing work is never overwritten')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.resume-', dir=destination.parent))
    try:
        for info in manifest['repositories']:
            name = info['name']
            if Path(name).name != name or name in ('.', '..'):
                raise RuntimeError('Invalid repository name')
            snap, repo = source / name, stage / name
            run(['git', 'clone', snap / 'repository.bundle', repo])
            run(['git', '-C', repo, 'checkout', '--detach', info['head']])
            if info['branch']:
                run(['git', '-C', repo, 'checkout', '-B', info['branch'], info['head']])
            if info['remote']:
                git(repo, 'remote', 'set-url', 'origin', info['remote'])
            else:
                git(repo, 'remote', 'remove', 'origin')
            for filename, extra in [('index.patch', ['--index']), ('worktree.patch', [])]:
                patch = snap / filename
                if patch.stat().st_size:
                    run(['git', '-C', repo, 'apply', *extra, patch])
            untracked = snap / 'untracked'
            if untracked.exists():
                for p in untracked.rglob('*'):
                    if p.is_file():
                        target = repo / p.relative_to(untracked)
                        if not target.resolve().is_relative_to(repo.resolve()) or target.exists():
                            raise RuntimeError('Unsafe untracked recovery path')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, target)
        shutil.copy2(source / 'manifest.json', stage / 'recovery-manifest.json')
        if (source / '_jobs').exists():
            shutil.copytree(source / '_jobs', stage / '_previous-job-reports')
        stage.rename(destination)
    except Exception:
        shutil.rmtree(stage)
        raise
    print(f'RECOVERED_ROOT={destination}')
    print('Previous processes are not restarted. Review reports before rerunning commands.')


def unpack(archive, destination):
    """Only regular files/directories; never follow symlinks or extract devices."""
    with tarfile.open(archive) as tar:
        for item in tar.getmembers():
            path = Path(destination) / item.name
            if not path.resolve().is_relative_to(Path(destination).resolve()) or not (item.isfile() or item.isdir()):
                raise RuntimeError('Unsafe archive member')
        tar.extractall(destination, filter='data')


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest='action', required=True)
    a = s.add_parser('save'); a.add_argument('reason', nargs='?', default='manual')
    a = s.add_parser('resume'); a.add_argument('source'); a.add_argument('destination')
    a = s.add_parser('unpack'); a.add_argument('archive'); a.add_argument('destination')
    a = p.parse_args()
    if a.action == 'save': snapshot(a.reason)
    elif a.action == 'resume': resume(a.source, a.destination)
    else: unpack(a.archive, a.destination)


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'Checkpoint failed: {e}', file=sys.stderr)
        sys.exit(1)
