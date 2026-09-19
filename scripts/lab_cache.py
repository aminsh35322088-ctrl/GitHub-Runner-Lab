#!/usr/bin/env python3
"""Prune old dependency environments and completed job reports while idle."""
import argparse
import json
from pathlib import Path
import shutil
import time
from lab_common import kit, lock, path_env
from lab_jobs import all_jobs

p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--days',type=int,default=14)
a=p.parse_args()
root=path_env('AGENT_PROJECT_CACHE_ROOT',Path.home()/'.cache/agent-projects')
with lock(kit()/'jobs.lock'):
    if any(j['state'] in ('running','queued') for j in all_jobs()):
        raise SystemExit('Cache cleanup refused while managed jobs are active')
    for directory in root.glob('**/node-*'):
        if directory.is_symlink() or not directory.is_dir():continue
        marker=directory/'.agent-lab-ready'
        if marker.exists() and time.time()-marker.stat().st_mtime>a.days*86400:
            print(('DELETE ' if a.apply else 'WOULD_DELETE ')+str(directory))
            if a.apply:shutil.rmtree(directory)
    cutoff=time.time()-a.days*86400
    for result in path_env('AGENT_JOBS_DIR',Path.home()/'agent-jobs').glob('*/result.json'):
        try:
            metadata=json.loads(result.read_text())
        except (OSError,ValueError):
            continue
        if metadata.get('state') not in ('queued','running') and result.stat().st_mtime < cutoff:
            print(('DELETE ' if a.apply else 'WOULD_DELETE ')+str(result.parent))
            if a.apply:shutil.rmtree(result.parent)
