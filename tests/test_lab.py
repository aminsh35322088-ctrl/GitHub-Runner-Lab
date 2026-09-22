import datetime as dt
import io
import json
import os
import signal
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

    def test_workspace_sets_repo_local_identity_from_github_actor(self):
        remote = self.base / 'remote-identity.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', remote], check=True, capture_output=True)
        seed = self.base / 'seed-identity'; init_repo(seed, remote)
        dest = self.base / 'workspaces' / 'identity'
        env = {**self.env, 'GITHUB_ACTOR': 'octocat', 'GITHUB_ACTOR_ID': '1234567'}
        result = command([SCRIPTS/'agent-workspace.sh', '--repo', remote, '--dir', dest], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(dest, 'config', '--local', '--get', 'user.name').stdout.strip(), 'octocat')
        self.assertEqual(git(dest, 'config', '--local', '--get', 'user.email').stdout.strip(),
                         '1234567+octocat@users.noreply.github.com')
        clean_env = {**env, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
        (dest/'identity.txt').write_text('works\n')
        self.assertEqual(command(['git', '-C', dest, 'add', 'identity.txt'], env=clean_env).returncode, 0)
        committed = command(['git', '-C', dest, 'commit', '-m', 'identity probe'], env=clean_env)
        self.assertEqual(committed.returncode, 0, committed.stderr)

    def test_workspace_repairs_corrupt_remote_tracking_ref_without_moving_head(self):
        remote = self.base / 'remote-ref.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', remote], check=True, capture_output=True)
        seed = self.base / 'seed-ref'; init_repo(seed, remote)
        dest = self.base / 'workspaces' / 'repair'
        args = [SCRIPTS/'agent-workspace.sh', '--repo', remote, '--dir', dest]
        self.assertEqual(command(args, env=self.env).returncode, 0)
        head = git(dest, 'rev-parse', 'HEAD').stdout.strip()
        ref = dest / '.git' / 'refs' / 'remotes' / 'origin' / 'main'
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text('not-a-valid-object-id\n')
        result = command(args, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(dest, 'rev-parse', 'HEAD').stdout.strip(), head)
        self.assertEqual(git(dest, 'rev-parse', 'refs/remotes/origin/main').stdout.strip(), head)

    def test_checkpoint_includes_explicitly_adopted_external_workspace(self):
        external = self.base / 'scratch' / 'external-repo'
        external.parent.mkdir()
        init_repo(external)
        (external/'tracked.txt').write_text('dirty external\n')
        (external/'new.rs').write_text('fn main() {}\n')
        adopted = command([SCRIPTS/'agent-workspace.sh', 'adopt', external], env=self.env)
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertIn('WORKSPACE_ADOPTED=', adopted.stdout)
        saved = command([SCRIPTS/'agent-checkpoint.sh', 'adopted-test'], env=self.env)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        source = (Path(self.env['AGENT_CHECKPOINT_DIR'])/'latest').resolve()
        manifest = json.loads((source/'manifest.json').read_text())
        entries = [x for x in manifest['repositories'] if x.get('source_path') == str(external.resolve())]
        self.assertEqual(len(entries), 1)
        recovered = self.base/'recovered-adopted'
        recovery_env = {**self.env, 'GITHUB_ACTOR': 'octocat', 'GITHUB_ACTOR_ID': '1234567'}
        result = command([sys.executable, SCRIPTS/'lab_checkpoint.py', 'resume', source, recovered],
                         env=recovery_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = recovered / entries[0]['name']
        self.assertEqual((restored/'tracked.txt').read_text(), 'dirty external\n')
        self.assertEqual((restored/'new.rs').read_text(), 'fn main() {}\n')
        self.assertEqual(git(restored, 'config', '--local', '--get', 'user.name').stdout.strip(),
                         'octocat')
        self.assertEqual(git(restored, 'config', '--local', '--get', 'user.email').stdout.strip(),
                         '1234567+octocat@users.noreply.github.com')

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

    def test_checkpoint_fails_closed_on_secret_in_tracked_patch(self):
        repo = Path(self.env['AGENT_WORKSPACE_ROOT'])/'secret-patch'
        init_repo(repo)
        (repo/'tracked.txt').write_text('github_pat_' + 'S'*30 + '\n')
        result = command([SCRIPTS/'agent-checkpoint.sh', 'secret-test'], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('secret-like data', result.stderr.lower())
        self.assertFalse((Path(self.env['AGENT_CHECKPOINT_DIR'])/'latest').exists())

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
        self.assertNotIn('command',report)
        self.assertFalse((Path(self.env['AGENT_JOBS_DIR'])/job/'command.json').exists())
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
        self.assertIn('lab_checkpoint.py" resume',sync)
        self.assertIn('recovery.env',sync)
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
        workspace=(SCRIPTS/'lab_workspace.py').read_text()
        helper=(SCRIPTS/'lab_git.py').read_text()
        self.assertIn('from lab_git import',workspace)
        self.assertIn("'git-auto'",helper)


    def test_validate_contract_runs_full_diagnoses_flake_and_cleans(self):
        workspace=self.base/'workspaces'/'demo';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        calls=self.base/'calls'
        hook.write_text(f'''#!/usr/bin/env bash
set -eu
echo "$1 $*" >> "{calls}"
case "$1" in
  full) echo tests/example.test.ts > "$AGENT_JOB_OUTPUT_DIR/failed-tests.txt"; exit 1 ;;
  test) exit 0 ;;
esac
''')
        hook.chmod(0o755)
        output=self.base/'validation'
        result=command([sys.executable,SCRIPTS/'lab_validate.py',workspace,'--output-dir',output],env=self.env)
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual(summary['classification'],'flaky')
        self.assertIn('full full',calls.read_text())
        self.assertIn('test test tests/example.test.ts',calls.read_text())
        self.assertIn('clean-materialized clean-materialized',calls.read_text())
        self.assertTrue((output/'summary.md').exists())

    def test_validate_contract_writes_success_summary(self):
        workspace=self.base/'workspaces'/'demo';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text(f'#!/usr/bin/env bash\nset -eu\ntest "$PWD" = "{workspace}"\nexit 0\n');hook.chmod(0o755)
        output=self.base/'validation'
        result=command([sys.executable,SCRIPTS/'lab_validate.py',workspace,'--output-dir',output],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr)
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual(summary['classification'],'passed')
        self.assertEqual([stage['name'] for stage in summary['stages']],['prepare','check','full'])

    def test_agent_run_exposes_managed_validation_and_guard_help(self):
        script=(SCRIPTS/'agent-run.sh').read_text()
        self.assertIn('validate)',script)
        self.assertIn('lab_runner_validate.py',script)
        self.assertIn('shell-help)',script)
        self.assertIn('Use agent-run.sh github',script)
        self.assertIn('git-sync)',script)
        self.assertIn('workspace adopt',script)

    def test_reliability_excludes_cancelled_keepalive_from_completion_rate(self):
        spec=importlib.util.spec_from_file_location('readme_status_keepalive',ROOT/'.github/scripts/readme-status.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        jobs={
            1:{'jobs':[{'name':'runner-lab','steps':[
                {'name':'Verify connection','status':'completed','conclusion':'success','completed_at':'2026-09-19T00:01:00Z'},
                {'name':'Prewarm Agent Toolchain and Keep RDC Lab Alive','status':'completed','conclusion':'cancelled','started_at':'2026-09-19T00:01:00Z'}]}]},
            2:{'jobs':[{'name':'runner-lab','steps':[
                {'name':'Verify connection','status':'completed','conclusion':'success','completed_at':'2026-09-19T05:01:00Z'},
                {'name':'Prewarm Agent Toolchain and Keep RDC Lab Alive','status':'completed','conclusion':'success','started_at':'2026-09-19T05:01:00Z'}]}]},
        }
        module.api=lambda path: jobs[int(path.split('/')[6])]
        runs=[{'id':1,'status':'completed','conclusion':'cancelled','created_at':'2026-09-19T00:00:00Z'},
              {'id':2,'status':'completed','conclusion':'success','created_at':'2026-09-19T05:00:00Z'}]
        stats=module.reliability('x/y',runs)
        self.assertEqual(stats['keepalive_rate'],100)
        self.assertEqual(stats['keepalive_sample'],1)

    def test_readme_status_marks_stale_snapshots(self):
        spec=importlib.util.spec_from_file_location('readme_status_stale',ROOT/'.github/scripts/readme-status.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        state=module.unknown_state(dt.datetime(2026,9,19,tzinfo=dt.timezone.utc))
        state['stale']=True
        self.assertIn('Status freshness',module.render(state,'x/y'))


    def test_validate_sigterm_runs_cleanup_and_writes_interrupted_summary(self):
        workspace=self.base/'workspaces'/'interrupt';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text('''#!/usr/bin/env bash
set -eu
case "$1" in
  prepare) sleep 30 ;;
  clean-materialized) touch "$AGENT_PROJECT_ROOT/cleanup-ran" ;;
esac
''');hook.chmod(0o755)
        output=self.base/'interrupted-validation'
        env={**self.env,'AGENT_PROJECT_ROOT':workspace}
        process=subprocess.Popen([sys.executable,SCRIPTS/'lab_validate.py',workspace,'--output-dir',output],
                                 cwd=ROOT,env={**os.environ,**{k:str(v) for k,v in env.items()}},
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(0.4);process.send_signal(signal.SIGTERM)
        process.communicate(timeout=8)
        self.assertEqual(process.returncode,128+signal.SIGTERM)
        self.assertTrue((workspace/'cleanup-ran').exists())
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual(summary['classification'],'interrupted')
        self.assertEqual(summary['interrupted_by'],'SIGTERM')
        self.assertEqual(summary['cleanup']['exit_code'],0)

    def test_validate_caps_stage_logs_and_reports_truncation(self):
        workspace=self.base/'workspaces'/'logs';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text('''#!/usr/bin/env bash
set -eu
if [ "$1" = prepare ]; then python3 -c 'print("x"*200000)'; fi
''');hook.chmod(0o755)
        output=self.base/'bounded-validation'
        result=command([sys.executable,SCRIPTS/'lab_validate.py',workspace,'--output-dir',output,
                        '--log-max-mb','0.01'],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr)
        summary=json.loads((output/'summary.json').read_text())
        prepare=summary['stages'][0]
        self.assertTrue(prepare['log_truncated'])
        self.assertLessEqual((output/'prepare.log').stat().st_size,summary['log_max_bytes'])
        self.assertIn('clean-materialized',(output/'summary.md').read_text())

    def test_validate_holds_legacy_dependency_lock_for_entire_contract(self):
        workspace=self.base/'workspaces'/'legacy';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text('''#!/usr/bin/env bash
set -eu
# compatibility marker: /app/node_modules
(
  exec 9>"$AGENT_PROJECT_CACHE_ROOT/legacy-app-node-modules.lock"
  if flock -n 9; then exit 41; else exit 0; fi
)
''');hook.chmod(0o755)
        cache=self.base/'project-cache';cache.mkdir()
        env={**self.env,'AGENT_JOB_ID':'direct','AGENT_PROJECT_CACHE_ROOT':cache}
        result=command([SCRIPTS/'agent-project.sh','validate',workspace],env=env)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_project_job_preserves_persistent_cache_root_across_clean_boundary(self):
        workspace=self.base/'workspaces'/'cache-root';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text('''#!/usr/bin/env bash
set -eu
printf '%s' "$AGENT_PROJECT_CACHE_ROOT" > "$AGENT_PROJECT_ROOT/cache-root.txt"
''');hook.chmod(0o755)
        cache=self.base/'persistent-project-cache';cache.mkdir()
        env={**self.env,'AGENT_PROJECT_CACHE_ROOT':cache}
        result=command([SCRIPTS/'agent-project.sh','prepare',workspace],env=env,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((workspace/'cache-root.txt').read_text(),str(cache))

    def test_managed_job_records_configurable_grace_window(self):
        workspace=Path(self.env['AGENT_WORKSPACE_ROOT'])/'grace';init_repo(workspace)
        result=command([sys.executable,SCRIPTS/'lab_jobs.py','start','--cwd',workspace,'--timeout','10',
                        '--grace-seconds','17','--','true'],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr);job=result.stdout.strip()
        waited=command([sys.executable,SCRIPTS/'lab_jobs.py','wait',job],env=self.env)
        self.assertEqual(waited.returncode,0,waited.stderr)
        self.assertEqual(json.loads(waited.stdout)['grace_seconds'],17)


    def test_toolset_manifest_drives_commands_and_profiles(self):
        manifest=ROOT/'config/runner-toolset.json'
        self.assertTrue(manifest.is_file())
        data=json.loads(manifest.read_text())
        self.assertEqual(data['schema_version'],1)
        result=command([sys.executable,SCRIPTS/'lab_toolset.py','commands','full'])
        self.assertEqual(result.returncode,0,result.stderr)
        commands=set(result.stdout.split())
        for name in ('git','gh','node','npm','python3','cmake','ninja','clang','ffmpeg','convert','shellcheck'):
            self.assertIn(name,commands)
        packages=command([sys.executable,SCRIPTS/'lab_toolset.py','packages','full'])
        self.assertEqual(packages.returncode,0,packages.stderr)
        package_names=set(packages.stdout.split())
        self.assertIn('build-essential',package_names)
        self.assertIn('ffmpeg',package_names)
        for name in ('libgtk-3-dev','libgstreamer1.0-dev','libgstreamer-plugins-base1.0-dev',
                     'libpulse-dev','libxdo-dev','libyuv-dev','libvpx-dev','libopus-dev','libaom-dev'):
            self.assertIn(name,package_names)

        modules=command([sys.executable,SCRIPTS/'lab_toolset.py','pkg-config-modules','full'])
        self.assertEqual(modules.returncode,0,modules.stderr)
        for name in ('glib-2.0','gtk+-3.0','gstreamer-1.0','gstreamer-app-1.0','libpulse','libyuv'):
            self.assertIn(name,modules.stdout.split())

    def test_toolset_manifest_pins_goss_with_checksums(self):
        result=command([sys.executable,SCRIPTS/'lab_toolset.py','goss','x86_64'])
        self.assertEqual(result.returncode,0,result.stderr)
        meta=json.loads(result.stdout)
        self.assertEqual(meta['version'],'0.4.10')
        self.assertEqual(meta['sha256'],'26e365428946294bcec0c61d867bb3c8349f39feb3d0e6f59084e98632785cc7')
        arm=command([sys.executable,SCRIPTS/'lab_toolset.py','goss','aarch64'])
        self.assertEqual(arm.returncode,0,arm.stderr)
        self.assertEqual(json.loads(arm.stdout)['sha256'],'90a59612b4d67d9f1a9038634c000790136bb82526a69de1e81ac075c2f6d2c6')
        bad=command([sys.executable,SCRIPTS/'lab_toolset.py','goss','mips'])
        self.assertNotEqual(bad.returncode,0)

    def test_agent_lib_reads_commands_from_toolset_manifest(self):
        content=(SCRIPTS/'agent-lib.sh').read_text()
        self.assertIn('lab_toolset.py',content)
        self.assertNotIn('git gh node npm python3 rg fd jq',content)

    def test_bootstrap_reads_packages_from_toolset_manifest(self):
        content=(SCRIPTS/'agent-bootstrap.sh').read_text()
        self.assertIn('lab_toolset.py',content)
        self.assertNotIn('CORE_PACKAGES=(',content)
        self.assertNotIn('BUILD_PACKAGES=(',content)
        self.assertNotIn('MEDIA_PACKAGES=(',content)


    def test_runner_validation_builds_hard_and_advisory_goss_specs(self):
        spec=importlib.util.spec_from_file_location('lab_runner_validate',SCRIPTS/'lab_runner_validate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        toolset=json.loads((ROOT/'config/runner-toolset.json').read_text())
        hard,advisory=module.build_specs(toolset,home=self.base,cache=self.base/'cache',require_rdc=False)
        self.assertIn('github.com',hard['dns'])
        self.assertIn('https://api.github.com',hard['http'])
        self.assertIn('https://registry.npmjs.org',advisory['http'])
        rendered=json.dumps(hard)
        self.assertIn('disk_free_gb',rendered)
        self.assertIn('inode_free_percent',rendered)
        self.assertIn('command -v git',rendered)
        self.assertIn('node --version',rendered)
        self.assertIn('pkg-config --exists glib-2.0',rendered)
        self.assertIn('pkg-config --exists gtk+-3.0',rendered)
        self.assertIn('pkg-config --exists gstreamer-1.0',rendered)
        self.assertIn('pkg-config --exists libpulse',rendered)
        self.assertIn('pkg-config --exists libyuv',rendered)

    def test_runner_validation_reports_degraded_advisory_without_failing(self):
        fake=self.base/'fake-goss'
        fake.write_text("#!/usr/bin/env python3\nimport pathlib,sys\npath=pathlib.Path(sys.argv[sys.argv.index('-g')+1])\nraise SystemExit(1 if 'registry.npmjs.org' in path.read_text() else 0)\n")
        fake.chmod(0o755)
        out=self.base/'runner-report'
        result=command([sys.executable,SCRIPTS/'lab_runner_validate.py','quick','--goss-bin',fake,
                        '--skip-smoke','--output-dir',out],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['classification'],'degraded')
        self.assertEqual(summary['stages']['host']['status'],'passed')
        self.assertEqual(summary['stages']['network_advisory']['status'],'degraded')
        self.assertTrue((out/'summary.md').exists())
        self.assertTrue((out/'junit.xml').exists())

    def test_runner_validation_fails_on_hard_goss_failure(self):
        fake=self.base/'fake-goss'
        fake.write_text("#!/bin/sh\nexit 1\n");fake.chmod(0o755)
        out=self.base/'runner-report-fail'
        result=command([sys.executable,SCRIPTS/'lab_runner_validate.py','quick','--goss-bin',fake,
                        '--skip-smoke','--output-dir',out],env=self.env)
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['classification'],'failed')
        self.assertEqual(summary['stages']['host']['status'],'failed')

    def test_goss_installer_rejects_unsupported_architecture_before_download(self):
        result=command([SCRIPTS/'install-goss.sh'],env={**self.env,'GOSS_ARCH':'mips'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unsupported goss architecture',result.stderr)
        self.assertNotIn('latest',(SCRIPTS/'install-goss.sh').read_text())


    def test_goss_v0410_dns_spec_uses_supported_attributes_only(self):
        spec=importlib.util.spec_from_file_location('lab_runner_validate_compat',SCRIPTS/'lab_runner_validate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        toolset=json.loads((ROOT/'config/runner-toolset.json').read_text())
        hard,_=module.build_specs(toolset,home=self.base,cache=self.base,require_rdc=False)
        dns=hard['dns']['github.com']
        self.assertEqual(set(dns),{'resolvable','timeout'})
        self.assertNotIn('retry_count',dns)
        self.assertNotIn('retry_delay',dns)

    def test_generated_resource_commands_are_shell_safe(self):
        spec=importlib.util.spec_from_file_location('lab_runner_validate_shell',SCRIPTS/'lab_runner_validate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        toolset=json.loads((ROOT/'config/runner-toolset.json').read_text())
        toolset['thresholds']={'disk_free_gb':0,'inode_free_percent':0}
        hard,_=module.build_specs(toolset,home=self.base,cache=self.base,require_rdc=False)
        for name in ('disk_free_gb','inode_free_percent'):
            result=subprocess.run(hard['command'][name]['exec'],shell=True,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,f"{name}: {result.stderr}")


    def test_smoke_profiles_are_bounded_and_manifest_complete(self):
        spec=importlib.util.spec_from_file_location('lab_smoke',SCRIPTS/'lab_smoke.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.stage_names('quick'),['native','cmake_ninja','node','python','git'])
        self.assertEqual(module.stage_names('full'),
                         ['native','cmake_ninja','node','python','git','docker','media'])
        commands=command([sys.executable,SCRIPTS/'lab_toolset.py','commands','full'])
        self.assertEqual(commands.returncode,0,commands.stderr)
        self.assertIn('ffprobe',commands.stdout.split())
        self.assertIn('identify',commands.stdout.split())

    def test_smoke_failure_is_reported_and_scratch_is_cleaned(self):
        out=self.base/'smoke-failure'
        result=command([sys.executable,SCRIPTS/'lab_smoke.py','quick','--only','node',
                        '--output-dir',out],env={**self.env,'PATH':'/nonexistent'})
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['classification'],'failed')
        self.assertEqual(summary['stages']['node']['status'],'failed')
        self.assertFalse(any(p.name.startswith('work-') for p in out.iterdir()))

    def test_runner_validation_propagates_smoke_failure(self):
        fake_goss=self.base/'fake-goss'
        fake_goss.write_text('#!/bin/sh\nexit 0\n');fake_goss.chmod(0o755)
        fake_smoke=self.base/'fake-smoke.py'
        fake_smoke.write_text(
            "import json,pathlib,sys\n"
            "out=pathlib.Path(sys.argv[sys.argv.index('--output-dir')+1]);out.mkdir(parents=True,exist_ok=True)\n"
            "(out/'summary.json').write_text(json.dumps({'classification':'failed','stages':{'native':{'status':'failed'}}}))\n"
            "raise SystemExit(1)\n"
        )
        out=self.base/'runner-smoke-failure'
        result=command([sys.executable,SCRIPTS/'lab_runner_validate.py','quick',
                        '--goss-bin',fake_goss,'--smoke-script',fake_smoke,'--output-dir',out],
                       env=self.env)
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['classification'],'failed')
        self.assertEqual(summary['stages']['smoke']['status'],'failed')
        self.assertEqual(summary['stages']['smoke']['details']['native']['status'],'failed')


    def test_validate_router_preserves_legacy_and_adds_runner_project_full_modes(self):
        bindir=self.base/'router';bindir.mkdir()
        runner=bindir/'agent-run.sh';runner.write_text((SCRIPTS/'agent-run.sh').read_text());runner.chmod(0o755)
        project=bindir/'agent-project.sh'
        project.write_text('#!/bin/sh\nprintf "PROJECT:%s\n" "$*"\n');project.chmod(0o755)
        validator=bindir/'lab_runner_validate.py'
        validator.write_text('import sys\nprint("RUNNER:"+" ".join(sys.argv[1:]))\n')
        cases=[
            (['validate','/tmp/demo'],['PROJECT:validate /tmp/demo']),
            (['validate','project','/tmp/demo'],['PROJECT:validate /tmp/demo']),
            (['validate','runner','quick','--skip-smoke'],['RUNNER:quick --skip-smoke']),
            (['validate','runner','full'],['RUNNER:full']),
            (['validate','full','/tmp/demo'],['RUNNER:full','PROJECT:validate /tmp/demo']),
        ]
        for args,expected in cases:
            result=command([runner,*args],cwd=bindir)
            self.assertEqual(result.returncode,0,(args,result.stderr))
            lines=[line for line in result.stdout.splitlines() if line]
            self.assertEqual(lines,expected,args)


    def test_prewarm_ready_is_gated_by_quick_runner_validation(self):
        bindir=self.base/'prewarm';bindir.mkdir()
        cache=self.base/'prewarm-cache';cache.mkdir()
        prewarm=bindir/'agent-prewarm.sh'
        prewarm.write_text((SCRIPTS/'agent-prewarm.sh').read_text());prewarm.chmod(0o755)
        (bindir/'agent-lib.sh').write_text(f"""#!/bin/bash
agent_cache_dir() {{ echo '{cache}'; }}
agent_status_file() {{ echo '{cache}/prewarm.env'; }}
agent_log_file() {{ echo '{cache}/prewarm.log'; }}
agent_toolchain_version() {{ echo test-v1; }}
agent_status_value() {{ :; }}
agent_full_toolchain_ready() {{ return 0; }}
agent_missing_full_commands() {{ :; }}
""")
        bootstrap=bindir/'agent-bootstrap.sh';bootstrap.write_text('#!/bin/sh\nexit 0\n');bootstrap.chmod(0o755)
        validator=bindir/'lab_runner_validate.py'
        validator.write_text(
            "import os,pathlib,sys\n"
            "pathlib.Path(os.environ['VALIDATE_RECORD']).write_text(' '.join(sys.argv[1:]))\n"
            "raise SystemExit(int(os.environ.get('VALIDATE_EXIT','0')))\n"
        )
        record=self.base/'validate-args'
        failed=command([prewarm,'--foreground'],
                       env={**self.env,'VALIDATE_RECORD':record,'VALIDATE_EXIT':'23'})
        self.assertEqual(failed.returncode,23,failed.stderr)
        state=(cache/'prewarm.env').read_text()
        self.assertIn('STATUS=FAILED',state)
        self.assertEqual(record.read_text().split()[0],'quick')
        passed=command([prewarm,'--foreground'],
                       env={**self.env,'VALIDATE_RECORD':record,'VALIDATE_EXIT':'0'})
        self.assertEqual(passed.returncode,0,passed.stderr)
        self.assertIn('STATUS=READY',(cache/'prewarm.env').read_text())

    def test_doctor_uses_manifest_as_toolchain_source_of_truth(self):
        content=(SCRIPTS/'agent-doctor.sh').read_text()
        self.assertIn('lab_toolset.py',content)
        self.assertIn('agent_required_full_commands',content)
        self.assertNotIn('tools: git=%s node=%s npm=%s',content)
        result=command([SCRIPTS/'agent-doctor.sh',self.base],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('toolset=VALID',result.stdout)
        expected_version=json.loads((ROOT/'config/runner-toolset.json').read_text())['toolchain_version']
        self.assertIn(f'toolchain_version={expected_version}',result.stdout)
        self.assertIn('tool.git=',result.stdout)


    def test_runner_reports_stable_toolchain_failure_category(self):
        fake=self.base/'categorizing-goss'
        fake.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "print(json.dumps({'results':[{'resource-id':'tool_git','resource-type':'Command','successful':False}]}))\n"
            "raise SystemExit(1)\n"
        );fake.chmod(0o755)
        out=self.base/'categorized-runner'
        result=command([sys.executable,SCRIPTS/'lab_runner_validate.py','quick','--goss-bin',fake,
                        '--skip-smoke','--output-dir',out],env=self.env)
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['stages']['host']['failure_categories'],['TOOLCHAIN'])
        self.assertEqual(summary['failure_categories'],['TOOLCHAIN'])

    def test_smoke_stages_have_stable_failure_categories(self):
        spec=importlib.util.spec_from_file_location('lab_smoke_categories',SCRIPTS/'lab_smoke.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        expected={
            'native':'TOOLCHAIN','cmake_ninja':'TOOLCHAIN','node':'TOOLCHAIN',
            'python':'TOOLCHAIN','git':'TOOLCHAIN','docker':'DOCKER','media':'MEDIA',
        }
        for stage,category in expected.items():
            self.assertEqual(module.stage_category(stage),category)

    def test_project_validation_stages_are_categorized(self):
        workspace=self.base/'workspaces'/'categories';init_repo(workspace)
        hook=workspace/'.github/agent-lab/runner.sh';hook.parent.mkdir(parents=True)
        hook.write_text('#!/bin/sh\nexit 0\n');hook.chmod(0o755)
        out=self.base/'project-categories'
        result=command([sys.executable,SCRIPTS/'lab_validate.py',workspace,'--output-dir',out],env=self.env)
        self.assertEqual(result.returncode,0,result.stderr)
        summary=json.loads((out/'summary.json').read_text())
        self.assertTrue(all(stage['category']=='PROJECT' for stage in summary['stages']))
        self.assertEqual(summary['cleanup']['category'],'CLEANUP')

    def test_fault_injection_rejects_bad_manifest_and_missing_command(self):
        bad=self.base/'bad-toolset.json'
        bad.write_text(json.dumps({'schema_version':99}))
        invalid=command([sys.executable,SCRIPTS/'lab_toolset.py','--manifest',bad,'validate'])
        self.assertNotEqual(invalid.returncode,0)
        spec=importlib.util.spec_from_file_location('lab_runner_validate_missing',SCRIPTS/'lab_runner_validate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        toolset=json.loads((ROOT/'config/runner-toolset.json').read_text())
        toolset['commands']['core'].append('definitely-not-a-runner-command')
        hard,_=module.build_specs(toolset,home=self.base,cache=self.base,require_rdc=False)
        missing=hard['command']['tool_definitely-not-a-runner-command']['exec']
        self.assertNotEqual(subprocess.run(missing,shell=True).returncode,0)

    def test_goss_installer_fails_closed_on_checksum_mismatch(self):
        fakebin=self.base/'fakebin';fakebin.mkdir()
        fakecurl=fakebin/'curl'
        fakecurl.write_text("""#!/bin/sh
out=''
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then shift; out="$1"; fi
  shift
done
printf corrupt > "$out"
""");fakecurl.chmod(0o755)
        cache=self.base/'goss-corrupt'
        result=command([SCRIPTS/'install-goss.sh'],env={
            **self.env,'PATH':f"{fakebin}:{os.environ.get('PATH','')}",'GOSS_CACHE_DIR':cache
        })
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((cache/'goss').exists())

    def test_docker_and_media_faults_are_classified_and_cleaned(self):
        for stage in ('docker','media'):
            out=self.base/f'fault-{stage}'
            result=command([sys.executable,SCRIPTS/'lab_smoke.py','full','--only',stage,
                            '--output-dir',out],env={**self.env,'PATH':'/nonexistent'})
            self.assertNotEqual(result.returncode,0,stage)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['stages'][stage]['status'],'failed')
            self.assertIn(summary['stages'][stage]['category'],('DOCKER','MEDIA'))
            self.assertFalse(any(p.name.startswith('work-') for p in out.iterdir()))


    def test_runner_reports_stable_rdc_failure_category(self):
        fake=self.base/'rdc-failing-goss'
        fake.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "print(json.dumps({'results':[{'resource-id':'rdc_health','resource-type':'Command','successful':False}]}))\n"
            "raise SystemExit(1)\n"
        );fake.chmod(0o755)
        out=self.base/'rdc-category'
        result=command([sys.executable,SCRIPTS/'lab_runner_validate.py','quick','--goss-bin',fake,
                        '--skip-smoke','--require-rdc','--output-dir',out],env=self.env)
        self.assertNotEqual(result.returncode,0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['stages']['host']['failure_categories'],['RDC'])
        self.assertEqual(summary['failure_categories'],['RDC'])


if __name__=='__main__': unittest.main()
