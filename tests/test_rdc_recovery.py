import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RecoveryTests(unittest.TestCase):
    def watchdog(self, age):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            now=dt.datetime.now(dt.timezone.utc)
            iso=lambda m: (now-dt.timedelta(minutes=m)).strftime('%Y-%m-%dT%H:%M:%SZ')
            runs={'workflow_runs':[{'id':i,'status':'completed','conclusion':'failure',
                   'created_at':iso(age+i*5+1),'run_started_at':iso(age+i*5+1),
                   'updated_at':iso(age+i*5)} for i in range(3)]}
            (base/'curl').write_text("#!/bin/sh\nprintf '%s' '"+json.dumps(runs)+"'\n")
            (base/'curl').chmod(0o755)
            return subprocess.run(['bash',str(ROOT/'.github/scripts/ensure-rdc-lab.sh')],
                env={**os.environ,'PATH':str(base)+':'+os.environ['PATH'],'GH_TOKEN':'dummy',
                     'REPO':'x/y','DRY_RUN':'true'},capture_output=True,text=True)

    def test_recent_crash_loop_stays_blocked(self):
        self.assertEqual(self.watchdog(2).returncode,21)

    def test_old_crash_loop_allows_one_recovery_probe(self):
        result=self.watchdog(70)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('DRY_RUN: would dispatch',result.stderr)

    def test_installer_retries_transient_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); count=base/'count'
            (base/'npm').write_text(f'#!/bin/sh\nn=$(cat "{count}" 2>/dev/null || echo 0)\nn=$((n+1))\necho "$n" > "{count}"\n[ "$n" -ge 2 ]\n')
            (base/'sleep').write_text('#!/bin/sh\nexit 0\n')
            for p in (base/'npm',base/'sleep'):p.chmod(0o755)
            result=subprocess.run(['bash',str(ROOT/'scripts/install-rdc.sh')],
                env={**os.environ,'PATH':str(base)+':'+os.environ['PATH']},capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(count.read_text().strip(),'2')
