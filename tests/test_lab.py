import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import lab_checkpoint
import importlib.util


def command(args, *, cwd=ROOT, env=None, timeout=30):
    merged = os.environ.copy()
    if env: merged.update({k: str(v) for k, v in env.items()})
    return subprocess.run([str(x) for x in args], cwd=cwd, env=merged,
                          capture_output=True, text=True, timeout=timeout)


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], check=check,
                          capture_output=True, text=True)


def init_repo(path, remote=None):
    subprocess.run(['git', 'init', '--initial-branch=main', str(path)], check=True, capture_output=True)
    git(path, 'config', 'user.name', 'Lab Test')
    git(path, 'config', 'user.email', 'lab@example.invalid')
    (path / 'tracked.txt').write_text('base\n')
    git(path, 'add', '.'); git(path, 'commit', '-m', 'base')
    if remote:
        git(path, 'remote', 'add', 'origin', str(remote))
        git(path, 'push', '-u', 'origin', 'main')


class LabTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-lab-test-')
        self.base = Path(self.temp.name)
        self.env = {
            'AGENT_WORKSPACE_ROOT': self.base / 'workspaces',
            'AGENT_CHECKPOINT_DIR': self.base / 'checkpoints',
            'AGENT_JOBS_DIR': self.base / 'jobs',
            'AGENT_KIT_CACHE_DIR': self.base / 'kit',
            'RDC_STATE_KEY': 'unit-test-key-with-more-than-32-characters',
        }
        Path(self.env['AGENT_WORKSPACE_ROOT']).mkdir()
        Path(self.env['AGENT_KIT_CACHE_DIR']).mkdir()
        now = int(time.time())
        (Path(self.env['AGENT_KIT_CACHE_DIR']) / 'runtime.env').write_text(
            f'START_EPOCH={now}\nHANDOFF_EPOCH={now+3600}\nAUTO_HANDOFF_MINUTES=20\n')

    def tearDown(self): self.temp.cleanup()

    def test_workspace_preserves_clean_unpublished_commit(self):
        remote = self.base / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', remote], check=True, capture_output=True)
        seed = self.base / 'seed'; init_repo(seed, remote)
        dest = self.base / 'workspaces' / 'demo'
        args = [SCRIPTS/'agent-workspace.sh', '--repo', remote, '--dir', dest]
        self.assertEqual(command(args, env=self.env).returncode, 0)
        git(dest, 'config', 'user.name', 'Lab Test')
        git(dest, 'config', 'user.email', 'lab@example.invalid')
        (dest/'tracked.txt').write_text('local\n'); git(dest,'add','.'); git(dest,'commit','-m','local')
        head = git(dest, 'rev-parse', 'HEAD').stdout.strip()
        result = command(args, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git(dest, 'rev-parse', 'HEAD').stdout.strip(), head)
        self.assertEqual((dest/'tracked.txt').read_text(), 'local\n')

    def test_checkpoint_roundtrip_includes_worktree_and_safe_untracked(self):
        primary = Path(self.env['AGENT_WORKSPACE_ROOT'])/'primary'; init_repo(primary)
        worktree = Path(self.env['AGENT_WORKSPACE_ROOT'])/'feature'
        git(primary, 'worktree', 'add', '-b', 'feature', str(worktree))
        (worktree/'tracked.txt').write_text('unstaged\n')
        (worktree/'new.ts').write_text('export const x = 1\n')
        (worktree/'token.txt').write_text('github_pat_' + 'A'*30)
        result = command([SCRIPTS/'agent-checkpoint.sh', 'test'], env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = (Path(self.env['AGENT_CHECKPOINT_DIR'])/'latest').resolve()
        manifest = json.loads((source/'manifest.json').read_text())
        self.assertEqual({x['name'] for x in manifest['repositories']}, {'primary','feature'})
        recovered = self.base/'recovered'
        result = command([sys.executable,SCRIPTS/'lab_checkpoint.py','resume',source,recovered],env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((recovered/'feature/tracked.txt').read_text(),'unstaged\n')
        self.assertEqual((recovered/'feature/new.ts').read_text(),'export const x = 1\n')
        self.assertFalse((recovered/'feature/token.txt').exists())

    def test_checkpoint_archive_authentication_detects_tamper(self):
        repo = Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo'; init_repo(repo)
        self.assertEqual(command([SCRIPTS/'agent-checkpoint.sh','test'],env=self.env).returncode,0)
        out=self.base/'artifacts'; env={**self.env,'AGENT_CHECKPOINT_ARTIFACT_DIR':out}
        self.assertEqual(command([SCRIPTS/'package-agent-checkpoints.sh'],env=env).returncode,0)
        archive=out/'latest.enc'; data=bytearray(archive.read_bytes());data[-1]^=1;archive.write_bytes(data)
        result=command([sys.executable,SCRIPTS/'lab_archive.py','decrypt',archive,self.base/'unpack'],env=env)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('authentication failed',result.stderr)

    def test_archive_unpack_rejects_path_traversal(self):
        archive=self.base/'bad.tar'
        with tarfile.open(archive,'w') as tar:
            payload=b'bad'; info=tarfile.TarInfo('../escape'); info.size=len(payload);tar.addfile(info,io.BytesIO(payload))
        with self.assertRaises(RuntimeError): lab_checkpoint.unpack(archive,self.base/'out')
        self.assertFalse((self.base/'escape').exists())

    def test_checkpoint_verify_rejects_unlisted_file(self):
        repo = Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo'; init_repo(repo)
        self.assertEqual(command([SCRIPTS/'agent-checkpoint.sh','test'],env=self.env).returncode,0)
        source=(Path(self.env['AGENT_CHECKPOINT_DIR'])/'latest').resolve()
        (source/'repo/untracked/injected.ts').parent.mkdir(parents=True,exist_ok=True)
        (source/'repo/untracked/injected.ts').write_text('injected')
        with self.assertRaises(RuntimeError): lab_checkpoint.verify(source)

    def test_checkpoint_job_report_omits_commands_and_output(self):
        repo=Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo';init_repo(repo)
        job=Path(self.env['AGENT_JOBS_DIR'])/'job-1';job.mkdir(parents=True)
        secret='github_pat_'+'Z'*40
        (job/'result.json').write_text(json.dumps({'id':'job-1','state':'success','exit_code':0,
                                                   'command':['echo',secret],'error':secret}))
        (job/'output.log').write_text(secret)
        self.assertEqual(command([SCRIPTS/'agent-checkpoint.sh','test'],env=self.env).returncode,0)
        source=(Path(self.env['AGENT_CHECKPOINT_DIR'])/'latest').resolve()
        report=json.loads((source/'_jobs/job-1/result.json').read_text())
        self.assertEqual(report,{'id':'job-1','state':'success','exit_code':0})
        content=b''.join(p.read_bytes() for p in source.rglob('*') if p.is_file()).decode(errors='ignore')
        self.assertNotIn(secret,content)

    def test_managed_job_reports_success_and_strips_secrets(self):
        workspace=Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo';init_repo(workspace)
        env={**self.env,'TOP_SECRET_SHOULD_NOT_LEAK':'secret-value'}
        result=command([sys.executable,SCRIPTS/'lab_jobs.py','start','--cwd',workspace,'--timeout','10','--',
                        'python3','-c','import os;print(os.getenv("TOP_SECRET_SHOULD_NOT_LEAK","clean"))'],env=env)
        self.assertEqual(result.returncode,0,result.stderr); job=result.stdout.strip()
        waited=command([sys.executable,SCRIPTS/'lab_jobs.py','wait',job],env=env)
        self.assertEqual(waited.returncode,0,waited.stderr)
        report=json.loads(waited.stdout)
        self.assertEqual(report['state'],'success');self.assertEqual(report['exit_code'],0)
        self.assertEqual((Path(self.env['AGENT_JOBS_DIR'])/job/'output.log').read_text().strip(),'clean')
        self.assertIn('peak_group_rss_bytes',report)

    def test_managed_job_passes_only_explicit_environment_names(self):
        workspace=Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo';init_repo(workspace)
        env={**self.env,'ALLOWED_FIXTURE':'visible','BLOCKED_FIXTURE':'hidden'}
        code='import os;print(os.getenv("ALLOWED_FIXTURE"),os.getenv("BLOCKED_FIXTURE","blocked"))'
        result=command([sys.executable,SCRIPTS/'lab_jobs.py','start','--cwd',workspace,'--timeout','10',
                        '--pass-env','ALLOWED_FIXTURE','--','python3','-c',code],env=env)
        job=result.stdout.strip(); waited=command([sys.executable,SCRIPTS/'lab_jobs.py','wait',job],env=env)
        self.assertEqual(waited.returncode,0,waited.stderr)
        self.assertEqual((Path(self.env['AGENT_JOBS_DIR'])/job/'output.log').read_text().strip(),'visible blocked')
        report=json.loads(waited.stdout);self.assertEqual(report['passed_environment'],['ALLOWED_FIXTURE'])

    def test_managed_job_times_out_and_cleans_descendant(self):
        workspace=Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo';init_repo(workspace)
        result=command([sys.executable,SCRIPTS/'lab_jobs.py','start','--cwd',workspace,'--timeout','1','--',
                        'sh','-c','sleep 30 & wait'],env=self.env)
        job=result.stdout.strip(); waited=command([sys.executable,SCRIPTS/'lab_jobs.py','wait',job],env=self.env,timeout=15)
        report=json.loads(waited.stdout)
        self.assertEqual(report['state'],'failed');self.assertEqual(report['reason'],'timeout')

    def test_managed_job_caps_captured_output(self):
        workspace=Path(self.env['AGENT_WORKSPACE_ROOT'])/'repo';init_repo(workspace)
        result=command([sys.executable,SCRIPTS/'lab_jobs.py','start','--cwd',workspace,'--timeout','10',
                        '--max-log-mb','1','--','python3','-c','print("x"*1500000)'],env=self.env)
        job=result.stdout.strip(); waited=command([sys.executable,SCRIPTS/'lab_jobs.py','wait',job],env=self.env)
        report=json.loads(waited.stdout)
        self.assertEqual(report['state'],'success');self.assertTrue(report['log_truncated'])
        self.assertLessEqual((Path(self.env['AGENT_JOBS_DIR'])/job/'output.log').stat().st_size,1024*1024)

    def test_health_fails_without_live_fresh_heartbeat(self):
        result=command([SCRIPTS/'health.sh'],env={**self.env,'RDC_PID_FILE':self.base/'none','RDC_HEALTH_FILE':self.base/'health'})
        self.assertNotEqual(result.returncode,0);self.assertIn('UNAVAILABLE',result.stdout)

    def test_health_accepts_only_fresh_heartbeat_for_same_process(self):
        health=self.base/'health.json';pid=self.base/'pid';pid.write_text(str(os.getpid()))
        health.write_text(json.dumps({'pid':os.getpid(),'healthy':True,'sampled_at':time.time()*1000}))
        result=command([SCRIPTS/'health.sh'],env={**self.env,'RDC_PID_FILE':pid,'RDC_HEALTH_FILE':health})
        self.assertEqual(result.returncode,0,result.stderr)
        health.write_text(json.dumps({'pid':os.getpid(),'healthy':True,'sampled_at':time.time()*1000-21000}))
        self.assertNotEqual(command([SCRIPTS/'health.sh'],env={**self.env,'RDC_PID_FILE':pid,'RDC_HEALTH_FILE':health}).returncode,0)

    def test_watchdog_transient_api_failure_keeps_json_clean(self):
        bindir=self.base/'bin';bindir.mkdir();marker=self.base/'marker'
        (bindir/'sleep').write_text('#!/bin/sh\nexit 0\n')
        (bindir/'curl').write_text(f'''#!/bin/sh
+if [ ! -f "{marker}" ]; then touch "{marker}"; exit 7; fi
+printf '%s\\n' '{{"workflow_runs":[]}}'
+'''.replace('+',''))
        for p in bindir.iterdir():p.chmod(0o755)
        env={**self.env,'PATH':str(bindir)+os.pathsep+os.environ['PATH'],'GH_TOKEN':'dummy','REPO':'x/y','DRY_RUN':'true'}
        result=command([ROOT/'.github/scripts/ensure-rdc-lab.sh'],env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('DRY_RUN: would dispatch',result.stderr)

    def test_reliability_uses_verified_ready_times_and_excludes_pending(self):
        spec=importlib.util.spec_from_file_location('readme_status',ROOT/'.github/scripts/readme-status.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        jobs={
            1:{'jobs':[{'name':'runner-lab','steps':[
                {'name':'Verify connection','status':'completed','conclusion':'success','completed_at':'2026-09-19T00:01:00Z'},
                {'name':'Prewarm Agent Toolchain and Keep RDC Lab Alive','status':'completed','conclusion':'success','started_at':'2026-09-19T00:01:00Z','completed_at':'2026-09-19T05:10:00Z'},
                {'name':'Gracefully Stop RDC','status':'completed','conclusion':'success','started_at':'2026-09-19T05:10:00Z','completed_at':'2026-09-19T05:10:05Z'}]}]},
            2:{'jobs':[{'name':'runner-lab','steps':[
                {'name':'Verify connection','status':'completed','conclusion':'success','completed_at':'2026-09-19T05:12:00Z'},
                {'name':'Prewarm Agent Toolchain and Keep RDC Lab Alive','status':'completed','conclusion':'success','started_at':'2026-09-19T05:12:00Z','completed_at':'2026-09-19T10:20:00Z'},
                {'name':'Gracefully Stop RDC','status':'completed','conclusion':'success','started_at':'2026-09-19T10:20:00Z','completed_at':'2026-09-19T10:20:05Z'}]}]},
        }
        module.api=lambda path: jobs[int(path.split('/')[6])]
        runs=[{'id':1,'status':'completed','created_at':'2026-09-19T00:00:00Z'},
              {'id':2,'status':'completed','created_at':'2026-09-19T05:11:00Z'},
              {'id':3,'status':'pending','run_started_at':'2026-09-19T04:00:00Z','created_at':'2026-09-19T04:00:00Z'}]
        stats=module.reliability('x/y',runs)
        self.assertEqual(stats['handoff_sample'],1)
        self.assertEqual(stats['median_gap'],1.9)

    def test_workflow_wires_pinned_runtime_recovery_pat_and_final_checkpoint(self):
        workflow=(ROOT/'.github/workflows/rdc-lab.yml').read_text()
        self.assertIn('RDC_VERSION: 0.2.51',workflow)
        self.assertNotIn('desktop-commander@latest',workflow)
        self.assertIn('AGENT_GITHUB_TOKEN: ${{ secrets.AGENT_GITHUB_TOKEN }}',workflow)
        self.assertIn('./scripts/checkpoint-sync.sh restore',workflow)
        self.assertIn('./scripts/checkpoint-sync.sh save finalizer',workflow)
        self.assertIn('uses: actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830',workflow)
        self.assertIn('uses: actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830',workflow)
        self.assertIn('./scripts/agent-run.sh cache --apply --days 14',workflow)
        self.assertIn('./scripts/agent-github.sh remove',workflow)
        self.assertIn('20|21)',workflow)
        self.assertNotIn('@latest remote',(ROOT/'scripts/bootstrap-rdc.sh').read_text())
        sync=(SCRIPTS/'checkpoint-sync.sh').read_text()
        self.assertIn('--force-with-lease=',sync)
        self.assertNotIn('fetch --quiet --depth=1 origin "$BRANCH"',sync)

    def test_every_workflow_action_is_pinned_to_a_commit(self):
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            for line in path.read_text().splitlines():
                if 'uses:' in line:
                    ref=line.split('@',1)[-1].split()[0]
                    self.assertRegex(ref,r'^[0-9a-f]{40}$',f'unpinned action in {path}: {line}')

    def test_cache_cleanup_prunes_old_dependency_and_finished_job_reports(self):
        cache=self.base/'project-cache'; dependency=cache/'demo/node-deadbeef'
        dependency.mkdir(parents=True); marker=dependency/'.agent-lab-ready';marker.touch()
        jobs=Path(self.env['AGENT_JOBS_DIR']); finished=jobs/'old-job';finished.mkdir(parents=True)
        (finished/'result.json').write_text(json.dumps({'id':'old-job','state':'success','created':1,'finished':1}))
        old=time.time()-20*86400
        os.utime(marker,(old,old));os.utime(finished/'result.json',(old,old))
        env={**self.env,'AGENT_PROJECT_CACHE_ROOT':cache}
        result=command([sys.executable,SCRIPTS/'lab_cache.py','--apply','--days','14'],env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(dependency.exists());self.assertFalse(finished.exists())

    def test_legacy_project_hook_is_serialized_instead_of_rejected(self):
        script=(SCRIPTS/'agent-project.sh').read_text()
        self.assertNotIn('must use workspace-local dependencies',script)
        self.assertIn('legacy-app-node-modules.lock',script)

    def test_workspace_routes_remote_git_through_optional_auth_helper(self):
        self.assertIn("'git-auto'",(SCRIPTS/'lab_workspace.py').read_text())


if __name__=='__main__': unittest.main()
