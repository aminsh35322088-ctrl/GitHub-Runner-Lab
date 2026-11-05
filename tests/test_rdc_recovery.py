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
            base=Path(tmp); count=base/'count'; agent_home=base/'agent-home'
            node_bin=agent_home/'.node-runtime'/'bin'
            local_bin=agent_home/'.local'/'bin'
            node_bin.mkdir(parents=True); local_bin.mkdir(parents=True)
            fake_bin=base/'bin'; fake_bin.mkdir()

            # Simulate a transient npm registry failure, then a successful
            # install into the isolated Agent prefix (never the runner prefix).
            npm=node_bin/'npm'
            npm.write_text(f'''#!/usr/bin/env bash
n=$(cat "{count}" 2>/dev/null || echo 0)
n=$((n+1))
printf '%s\\n' "$n" > "{count}"
if [[ "$n" -lt 2 ]]; then exit 1; fi
install -m 0755 /bin/true "$HOME/.local/bin/desktop-commander"
''')
            npm.chmod(0o755)

            curl=fake_bin/'curl'
            curl.write_text('''#!/usr/bin/env bash
set -eu
while (($#)); do
  if [[ "$1" == "-o" ]]; then shift; : > "$1"; fi
  shift
done
''')
            openssl=fake_bin/'openssl'
            openssl.write_text('''#!/usr/bin/env bash
if [[ "$1" == "dgst" ]]; then printf 'digest-bytes'; exit 0; fi
if [[ "$1" == "base64" ]]; then printf 'cx7KTscLF951adHmDLVCDIVTqJ/Bg1LtdRILZoo5wa/UMeyOfMAuOJOEBulNMKBKb5Xd3nHDQnB9aXroN9Fw5A=='; exit 0; fi
exit 2
''')
            sudo=fake_bin/'sudo'
            sudo.write_text('''#!/usr/bin/env bash
set -eu
while [[ "$1" != "--" ]]; do shift; done
shift
exec "$@"
''')
            sleep=fake_bin/'sleep'; sleep.write_text(chr(10).join(['#!/bin/sh','exit 0','']))
            for tool in (curl,openssl,sudo,sleep): tool.chmod(0o755)

            result=subprocess.run(['bash',str(ROOT/'scripts/install-rdc.sh')],
                env={**os.environ,'PATH':str(fake_bin)+':'+os.environ['PATH'],
                     'RDC_AGENT_USER':'rdc-agent','RDC_AGENT_HOME':str(agent_home),
                     'AGENT_NPM_CACHE_DIR':str(base/'agent-npm-cache')},
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(count.read_text().strip(),'2')
            self.assertTrue((local_bin/'desktop-commander').is_file())
            self.assertIn('isolated Agent prefix',result.stdout)
