#!/usr/bin/env python3
"""Git safety helpers for Agent Lab workspaces."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from lab_common import SCRIPTS, git, run


def remote_git(*args, check=True):
    return run([SCRIPTS / 'agent-github.sh', 'git-auto', *args], check=check)


def _valid_branch(branch):
    result = run(['git', 'check-ref-format', '--branch', branch], check=False)
    if result.returncode:
        raise RuntimeError(f'Invalid branch name: {branch}')
    return branch


def _tracking_ref(remote, branch):
    _valid_branch(branch)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', remote):
        raise RuntimeError(f'Invalid remote name: {remote}')
    return f'refs/remotes/{remote}/{branch}'


def git_supports_show_ref_exists(repo):
    """Probe `git show-ref --exists`, which Git 2.42 added.

    Older Git answers 129 (usage error) for the unknown option, which would
    otherwise be indistinguishable from a genuine reference problem. The probe
    runs inside the target repository so repo discovery cannot mask it.
    """
    result = run(['git', '-C', repo, 'show-ref', '--exists',
                  'refs/heads/__lab_capability_probe__'], check=False)
    return result.returncode != 129


def tracking_ref_state(repo, remote, branch):
    if not git_supports_show_ref_exists(repo):
        raise RuntimeError(
            'Reference inspection requires Git 2.42 or newer '
            '(`git show-ref --exists`). Upgrade Git, then retry.'
        )
    ref = _tracking_ref(remote, branch)
    exists = run(['git', '-C', repo, 'show-ref', '--exists', ref], check=False)
    if exists.returncode == 2:
        return 'missing'
    if exists.returncode != 0:
        return 'corrupt'
    resolved = run(['git', '-C', repo, 'rev-parse', '--verify', '--quiet',
                    '--end-of-options', f'{ref}^{{commit}}'], check=False)
    return 'valid' if resolved.returncode == 0 else 'corrupt'


def remote_branch_oid(repo, remote, branch):
    _valid_branch(branch)
    result = remote_git('-C', repo, 'ls-remote', '--exit-code', '--heads',
                        remote, f'refs/heads/{branch}', check=False)
    if result.returncode:
        raise RuntimeError(f'Remote branch not found: {remote}/{branch}')
    lines = result.stdout.decode().splitlines()
    expected = f'refs/heads/{branch}'
    for line in lines:
        fields = line.split()
        if len(fields) == 2 and fields[1] == expected and re.fullmatch(r'[0-9a-fA-F]{40,64}', fields[0]):
            return fields[0].lower()
    raise RuntimeError(f'Could not verify remote branch: {remote}/{branch}')


def repair_tracking_ref(repo, remote, branch):
    repo = Path(repo).resolve()
    local_ref = _tracking_ref(remote, branch)
    remote_ref = f'refs/heads/{branch}'
    expected = remote_branch_oid(repo, remote, branch)

    # Verify the remote first, then remove only a broken loose ref using Git's
    # own path resolver. Never edit packed-refs and never move HEAD.
    if tracking_ref_state(repo, remote, branch) == 'corrupt':
        common_raw = Path(git(repo, 'rev-parse', '--git-common-dir'))
        common = common_raw if common_raw.is_absolute() else (repo / common_raw)
        common = common.resolve()
        path_raw = Path(git(repo, 'rev-parse', '--git-path', local_ref))
        ref_path = path_raw if path_raw.is_absolute() else (repo / path_raw)
        resolved = ref_path.resolve(strict=False)
        if not resolved.is_relative_to(common):
            raise RuntimeError('Ref path escaped the Git common directory')
        if ref_path.is_symlink():
            raise RuntimeError('Ref repair refuses symbolic loose refs')
        if ref_path.exists():
            ref_path.unlink()
        if tracking_ref_state(repo, remote, branch) == 'corrupt':
            raise RuntimeError('Corrupt ref is not a removable loose ref; refusing packed-ref mutation')

    scratch = f'refs/agent-lab/repair/{uuid.uuid4().hex}'
    try:
        # Empty --refmap disables configured remote-tracking mappings. This makes
        # repair independent of configured mappings while downloading one verified branch.
        remote_git('-C', repo, 'fetch', '--no-tags', '--refmap=', remote,
                   f'+{remote_ref}:{scratch}')
        fetched = git(repo, 'rev-parse', scratch).lower()
        if fetched != expected:
            raise RuntimeError('Fetched repair object does not match verified remote head')
        run(['git', '-C', repo, 'update-ref', local_ref, fetched])
        verified = git(repo, 'rev-parse', local_ref).lower()
        if verified != expected:
            raise RuntimeError('Remote-tracking ref repair verification failed')
    finally:
        run(['git', '-C', repo, 'update-ref', '-d', scratch], check=False)
    return expected


def fetch_with_tracking_repair(repo, remote='origin', branch_hint=None):
    repaired = False
    if branch_hint and tracking_ref_state(repo, remote, branch_hint) == 'corrupt':
        repair_tracking_ref(repo, remote, branch_hint)
        repaired = True
    result = remote_git('-C', repo, 'fetch', '--prune', remote, check=False)
    if result.returncode and branch_hint and tracking_ref_state(repo, remote, branch_hint) == 'corrupt':
        repair_tracking_ref(repo, remote, branch_hint)
        repaired = True
        result = remote_git('-C', repo, 'fetch', '--prune', remote, check=False)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args,
                                            output=result.stdout, stderr=result.stderr)
    return repaired


def _identity_from_env():
    name = os.environ.get('AGENT_GIT_NAME', '').strip()
    email = os.environ.get('AGENT_GIT_EMAIL', '').strip()
    if name or email:
        if not name or not email:
            raise RuntimeError('AGENT_GIT_NAME and AGENT_GIT_EMAIL must be provided together')
        return name, email, 'agent-env'

    login = os.environ.get('GITHUB_ACTOR', '').strip()
    actor_id = os.environ.get('GITHUB_ACTOR_ID', '').strip()
    if login and actor_id.isdigit():
        return login, f'{actor_id}+{login}@users.noreply.github.com', 'github-actor'
    return None


def _identity_from_authenticated_github():
    result = run([SCRIPTS / 'agent-github.sh', 'gh', 'api', 'user',
                  '--jq', '[.login,(.id|tostring)]|@tsv'], check=False)
    if result.returncode:
        return None
    fields = result.stdout.decode().strip().split('\t')
    if len(fields) != 2 or not fields[0] or not fields[1].isdigit():
        return None
    return fields[0], f'{fields[1]}+{fields[0]}@users.noreply.github.com', 'github-auth'


def ensure_repo_identity(repo):
    repo = Path(repo).resolve()
    existing_name = git(repo, 'config', '--local', '--get', 'user.name', check=False)
    existing_email = git(repo, 'config', '--local', '--get', 'user.email', check=False)
    if existing_name and existing_email:
        return existing_name, existing_email, 'existing-local'

    identity = _identity_from_env() or _identity_from_authenticated_github()
    if not identity:
        return None
    name, email, source = identity
    run(['git', '-C', repo, 'config', '--local', 'user.name', name])
    run(['git', '-C', repo, 'config', '--local', 'user.email', email])
    return name, email, source


def sync(repo, branch, remote='origin'):
    repo = Path(repo).resolve()
    before = git(repo, 'rev-parse', 'HEAD')
    repaired = fetch_with_tracking_repair(repo, remote, branch)
    ref = _tracking_ref(remote, branch)
    if tracking_ref_state(repo, remote, branch) != 'valid':
        # Missing refs are safe to build only after a verified explicit fetch.
        expected = remote_branch_oid(repo, remote, branch)
        scratch_repaired = repair_tracking_ref(repo, remote, branch)
        repaired = True
        if scratch_repaired != expected:
            raise RuntimeError('Remote-tracking ref verification mismatch')
    remote_head = git(repo, 'rev-parse', ref)
    after = git(repo, 'rev-parse', 'HEAD')
    if after != before:
        raise RuntimeError('Git sync moved HEAD unexpectedly')
    print(f'GIT_SYNC_REPAIRED={str(repaired).lower()}')
    print(f'GIT_SYNC_REMOTE_HEAD={remote_head}')
    print(f'GIT_SYNC_HEAD={after}')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    sync_parser = sub.add_parser('sync')
    sync_parser.add_argument('repo')
    sync_parser.add_argument('--branch', required=True)
    sync_parser.add_argument('--remote', default='origin')
    identity_parser = sub.add_parser('identity')
    identity_parser.add_argument('repo')
    args = parser.parse_args()

    if args.action == 'sync':
        sync(args.repo, args.branch, args.remote)
    else:
        identity = ensure_repo_identity(args.repo)
        if not identity:
            print('AGENT_GIT_IDENTITY=UNAVAILABLE')
            sys.exit(4)
        name, email, source = identity
        print(f'AGENT_GIT_IDENTITY={source}')
        print(f'AGENT_GIT_NAME={name}')
        print(f'AGENT_GIT_EMAIL={email}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Git workspace helper failed: {exc}', file=sys.stderr)
        sys.exit(3)
