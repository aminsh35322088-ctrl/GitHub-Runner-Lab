import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('readme_live', Path(__file__).resolve().parents[1] / '.github/scripts/readme-status.py')
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

class LiveStatusTest(unittest.TestCase):
    def test_active_push_run_takes_precedence_over_old_failure(self):
        runs = [dict(id=2, head_repository={'full_name':'x/y'}, event='push', status='in_progress', created_at='2026-10-14T01:00:00Z', html_url='https://example/2'), dict(id=1, head_repository={'full_name':'x/y'}, event='workflow_dispatch', status='completed', conclusion='failure', created_at='2026-10-14T00:00:00Z', html_url='https://example/1')]
        jobs = [{'name':'runner-lab', 'status':'in_progress', 'started_at':'2026-10-14T01:00:00Z', 'steps':[{'name':'Verify connection','conclusion':'success'}, {'name':status.KEEP_STEPS[0], 'status':'in_progress','started_at':'2026-10-14T01:01:00Z'}]}]
        def api(path):
            return {'workflow_runs':runs} if '/workflows/' in path else {'jobs':jobs}
        with patch.object(status, 'api', side_effect=api), patch.object(status, 'reliability', return_value={}), patch.object(status, 'workflow_limits', return_value=(330,350,20)):
            result=status.collect('x/y','main',dt.datetime(2026,10,14,1,2,tzinfo=dt.timezone.utc))
        self.assertEqual(result['state'],'running')
        self.assertEqual(result['run_id'],2)

class StatusCommitHistoryTest(unittest.TestCase):
    TIP = {'sha': 'a' * 40, 'name': 'github-actions[bot]',
           'email': '41898282+github-actions[bot]@users.noreply.github.com',
           'subject': 'docs: refresh runner status', 'files': ['README.md']}

    def test_own_status_tip_is_amendable(self):
        self.assertTrue(status.is_status_commit(self.TIP))

    def test_foreign_or_mixed_tips_are_not_amendable(self):
        for change in ({'name': 'Amin'}, {'email': 'amin@example.invalid'},
                       {'subject': 'docs: refresh runner status!'},
                       {'files': ['README.md', 'scripts/lab_jobs.py']},
                       {'files': []}):
            self.assertFalse(status.is_status_commit({**self.TIP, **change}), change)
        self.assertFalse(status.is_status_commit(None))

    def test_commit_message_records_audit_trail(self):
        state = {'checked': '2026-11-04T09:00:00+00:00', 'run_id': 123}
        message = status.status_commit_message(state, amend_of='b' * 40,
                                               previous_checked='2026-11-04 08:50:00 UTC')
        lines = message.splitlines()
        self.assertEqual(lines[0], 'docs: refresh runner status')
        self.assertIn('Status-Checked: 2026-11-04T09:00:00+00:00', lines)
        self.assertIn('Status-Run-Id: 123', lines)
        self.assertIn('Amends-Status-Commit: ' + 'b' * 40, lines)
        self.assertIn('Previous-Status-Last-Checked: 2026-11-04 08:50:00 UTC', lines)

    def test_plain_commit_message_has_no_amend_reference(self):
        message = status.status_commit_message({'checked': None, 'run_id': None})
        self.assertNotIn('Amends-Status-Commit', message)
        self.assertIn('Status-Checked: unknown', message)

if __name__ == '__main__': unittest.main()