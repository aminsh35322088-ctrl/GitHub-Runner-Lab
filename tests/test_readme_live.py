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

if __name__ == '__main__': unittest.main()