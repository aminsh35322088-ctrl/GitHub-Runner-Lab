#!/usr/bin/env python3
"""Read-only readiness report. Unknown credentials never count as verified."""
import json
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
stamp=kit()/'checkpoint-persisted-at'
report['last_durable_checkpoint']=stamp.read_text().strip() if stamp.exists() else None
try:
    p=subprocess.run(['bash',str(SCRIPTS/'agent-github.sh'),'gh','api','repos/aminsh35322088-ctrl/GitHub-Runner-Lab','--jq','.permissions'],capture_output=True,text=True,timeout=15)
    report['github']={'verified':p.returncode==0,'permissions':json.loads(p.stdout) if p.returncode==0 else None}
except (OSError, subprocess.TimeoutExpired, ValueError): report['github']={'verified':False}
report['ready']=report['runtime']['state']=='SAFE' and report['rdc']['ok'] and report['docker']['ok'] and all(report['tools'].values()) and report['free_disk_bytes']>2*1024**3
print(json.dumps(report,indent=2))
raise SystemExit(0 if report['ready'] else 1)
