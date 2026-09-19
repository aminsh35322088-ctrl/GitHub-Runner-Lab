#!/usr/bin/env python3
"""Authenticated encrypted checkpoint archive; key never appears in arguments."""
import argparse
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from lab_checkpoint import checkpoint_root, unpack, verify
from lab_common import path_env

MAGIC = b'AGENTLAB2\n'


def key():
    value = os.environ.get('RDC_STATE_KEY', '')
    if len(value) < 32: raise ValueError('RDC_STATE_KEY must contain at least 32 characters')
    return value.encode()


def crypt(source, target, decrypt=False):
    args = ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-salt',
            '-in', str(source), '-out', str(target), '-pass', 'stdin']
    if decrypt: args.append('-d')
    subprocess.run(args, input=key()+b'\n', check=True, capture_output=True)


def package():
    source = checkpoint_root() / 'latest'
    if not source.exists():
        print('No checkpoint to package'); return
    source = source.resolve(); verify(source)
    out = path_env('AGENT_CHECKPOINT_ARTIFACT_DIR', Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())) / '.agent-artifacts')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory() as tmp:
        plain, cipher = Path(tmp)/'snapshot.tar.gz', Path(tmp)/'cipher'
        with tarfile.open(plain, 'w:gz') as tar: tar.add(source, arcname='snapshot')
        if plain.stat().st_size > 40*1024*1024:
            raise ValueError('Checkpoint exceeds 40 MiB; push large work and reduce retained reports')
        crypt(plain, cipher)
        salt = os.urandom(16)
        payload = MAGIC + salt + cipher.read_bytes()
        mac_key = hashlib.pbkdf2_hmac('sha256', key(), b'lab-mac-'+salt, 200000)
        target = out / 'latest.enc'; temp = out / '.latest.tmp'
        temp.write_bytes(payload + hmac.digest(mac_key, payload, 'sha256'))
        temp.chmod(0o600); temp.replace(target)
    print(f'CHECKPOINT_ARCHIVE={target}')


def decrypt(archive, destination):
    data = Path(archive).read_bytes()
    if not data.startswith(MAGIC) or len(data) < 80: raise ValueError('Unsupported checkpoint format')
    payload, tag = data[:-32], data[-32:]
    salt = payload[len(MAGIC):len(MAGIC)+16]
    mac_key = hashlib.pbkdf2_hmac('sha256', key(), b'lab-mac-'+salt, 200000)
    if not hmac.compare_digest(tag, hmac.digest(mac_key, payload, 'sha256')):
        raise ValueError('Checkpoint authentication failed')
    dest = Path(destination)
    if dest.exists(): raise ValueError('Extraction destination must not exist')
    with tempfile.TemporaryDirectory() as tmp:
        cipher, plain = Path(tmp)/'cipher', Path(tmp)/'snapshot.tar.gz'
        cipher.write_bytes(payload[len(MAGIC)+16:]); crypt(cipher, plain, True)
        dest.mkdir(parents=True, mode=0o700)
        unpack(plain, dest)
        verify(dest/'snapshot')
    print(f'CHECKPOINT_SOURCE={dest / "snapshot"}')


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(); s = p.add_subparsers(dest='action', required=True)
    s.add_parser('package')
    a = s.add_parser('decrypt'); a.add_argument('archive'); a.add_argument('destination')
    a=p.parse_args()
    if a.action=='package': package()
    else: decrypt(a.archive,a.destination)


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'Archive failed: {e}', file=sys.stderr); sys.exit(1)
