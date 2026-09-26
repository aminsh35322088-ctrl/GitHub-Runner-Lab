#!/usr/bin/env python3
"""Read-only readiness report. Unknown credentials never count as verified."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from lab_common import SCRIPTS, kit, runtime
from lab_jobs import all_jobs

report = {'runtime': runtime(), 'free_disk_bytes': shutil.disk_usage(Path.home()).free,
          'tools': {x: bool(shutil.which(x)) for x in ['git','gh','node','npm','python3','docker','shellcheck']},
          'active_jobs': [j['id'] for j in all_jobs() if j['state'] in ('queued','running')]}
for key, command in [('rdc', ['bash',str(SCRIPTS/'health.sh')]),
                     ('docker', ['docker','info','--format','{{.ServerVersion}}'])]:
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=10)
        report[key]={'ok':p.returncode==0,'summary':p.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired): report[key]={'ok':False}
try:
    probe = f'source {str(SCRIPTS / "agent-lib.sh")!r}; agent_full_toolchain_ready'
    p=subprocess.run(['bash','-lc',probe],capture_output=True,text=True,timeout=20)
    report['toolchain']={'ok':p.returncode==0}
except (OSError, subprocess.TimeoutExpired):
    report['toolchain']={'ok':False}
stamp=kit()/'checkpoint-persisted-at'
report['last_durable_checkpoint']=stamp.read_text().strip() if stamp.exists() else None
repository = os.getenv('GITHUB_REPOSITORY')
if not repository:
    try:
        remote = subprocess.check_output(
            ['git', '-C', str(SCRIPTS.parent), 'remote', 'get-url', 'origin'],
            text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        match = re.search(r'github\.com[/:]([^/]+/[^/]+?)(?:\.git)?report['ready']=report['runtime']['state']=='SAFE' and report['rdc']['ok'] and report['docker']['ok'] and report['toolchain']['ok'] and all(report['tools'].values()) and report['free_disk_bytes']>2*1024**3
print(json.dumps(report,indent=2))
raise SystemExit(0 if report['ready'] else 1)
, remote)
        repository = match.group(1) if match else None
    except (OSError, subprocess.SubprocessError):
        repository = None
try:
    if repository:
        p=subprocess.run(['bash',str(SCRIPTS/'agent-github.sh'),'gh','api',f'repos/{repository}','--jq','.permissions'],capture_output=True,text=True,timeout=15)
        report['github']={'verified':p.returncode==0,'repository':repository,
                          'permissions':json.loads(p.stdout) if p.returncode==0 else None}
    else:
        p=subprocess.run(['bash',str(SCRIPTS/'agent-github.sh'),'status'],capture_output=True,text=True,timeout=15)
        report['github']={'verified':p.returncode==0,'repository':None,'permissions':None}
except (OSError, subprocess.TimeoutExpired, ValueError):
    report['github']={'verified':False,'repository':repository,'permissions':None}
report['ready']=report['runtime']['state']=='SAFE' and report['rdc']['ok'] and report['docker']['ok'] and report['toolchain']['ok'] and all(report['tools'].values()) and report['free_disk_bytes']>2*1024**3
print(json.dumps(report,indent=2))
raise SystemExit(0 if report['ready'] else 1)
