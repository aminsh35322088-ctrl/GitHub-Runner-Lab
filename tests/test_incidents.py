"""Incident transport is simulated; report content and decisions use production code."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'AGENT_KIT_CACHE_DIR': self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def module(self):
        import lab_incidents
        return lab_incidents

    def report(self):
        return self.module().make_report('TOOLCHAIN', 'missing-cmake', 'CMake missing',
                                        'Run cmake --version twice', 'version printed',
                                        'command not found', 2)

    def test_redacts_credentials_and_private_keys(self):
        mod = self.module()
        raw = ('Authorization: Bearer privatevalue\npassword=hunter2\n'
               'https://user:pass@example.org/x?token=abcdef\n'
               '-----BEGIN PRIVATE KEY-----\nprivatebody\n-----END PRIVATE KEY-----\n'
               'ghp_' + 'x' * 36)
        clean = mod.sanitize(raw)
        for secret in ('privatevalue', 'hunter2', 'user:pass', 'abcdef', 'privatebody', 'ghp_'):
            self.assertNotIn(secret, clean)

    def test_single_transient_failure_is_not_publishable(self):
        with self.assertRaises(ValueError):
            self.module().make_report('NETWORK', 'dns', 'DNS', 'lookup', 'IP', 'timeout', 1)

    def test_project_category_is_not_lab_incident(self):
        with self.assertRaises(ValueError):
            self.module().make_report('PROJECT', 'tests', 'Tests', 'run', 'pass', 'fail', 2)

    def test_duplicate_uses_existing_issue_and_suppresses_identical_evidence(self):
        mod = self.module()
        report = self.report()
        existing = {'number': 7, 'body': report['marker'] + '\n' + report['evidence_marker'],
                    'html_url': 'https://github.com/a/b/issues/7'}
        calls = []
        def api(method, endpoint, payload=None):
            calls.append((method, endpoint, payload))
            return [existing]
        with patch.object(mod, 'api', api):
            result = mod.publish(report)
        self.assertEqual(result['status'], 'duplicate')
        self.assertTrue(all(c[0] == 'GET' for c in calls))

    def test_search_error_never_creates_issue_and_keeps_pending_report(self):
        mod = self.module()
        with patch.object(mod, 'api', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                mod.publish(self.report())
        self.assertEqual(len(list((Path(self.tmp.name) / 'incidents').glob('*.json'))), 1)

    def test_new_evidence_comments_without_creating_duplicate(self):
        mod = self.module()
        report = self.report()
        calls = []
        def api(method, endpoint, payload=None):
            calls.append((method, endpoint, payload))
            if '/comments' in endpoint:
                return [] if method == 'GET' else {'html_url': 'https://github.com/a/b/issues/7#comment'}
            return [{'number': 7, 'body': report['marker'], 'html_url': 'https://github.com/a/b/issues/7'}]
        with patch.object(mod, 'api', api):
            self.assertEqual(mod.publish(report)['status'], 'commented')
        self.assertEqual([c[1] for c in calls if c[0] == 'POST'],
                         ['repos/' + mod.REPOSITORY + '/issues/7/comments'])

    def test_dry_run_has_no_api_calls(self):
        mod = self.module()
        with patch.object(mod, 'api', side_effect=AssertionError('network')):
            self.assertEqual(mod.publish(self.report(), dry_run=True)['status'], 'dry-run')

    def test_automatic_reporting_requires_consecutive_failures(self):
        mod = self.module()
        fail = {'classification': 'failed', 'failure_categories': ['NETWORK'], 'profile': 'quick'}
        good = {'failure_categories': [], 'profile': 'quick'}
        sent = []
        with patch.object(mod, 'publish', lambda report: sent.append(report)):
            mod.observe_validation(fail)
            self.assertEqual(sent, [])
            mod.observe_validation(good)
            mod.observe_validation(fail)
            self.assertEqual(sent, [])
            mod.observe_validation(fail)
        self.assertEqual(len(sent), 1)

    def test_ambiguous_write_failure_is_deduplicated_on_retry(self):
        mod = self.module()
        report = self.report()
        with patch.object(mod, 'api', side_effect=[[], TimeoutError('reply lost')]):
            with self.assertRaises(TimeoutError):
                mod.publish(report)
        existing = {'number': 9, 'body': report['body'], 'html_url': 'https://github.com/a/b/issues/9'}
        with patch.object(mod, 'api', return_value=[existing]):
            self.assertEqual(mod.publish(report)['status'], 'duplicate')
        self.assertEqual(list((Path(self.tmp.name) / 'incidents').glob('*.json')), [])

    def test_advisory_degradation_never_publishes_incident(self):
        mod = self.module()
        summary = {'classification': 'degraded', 'failure_categories': ['NETWORK'], 'profile': 'quick'}
        with patch.object(mod, 'publish', side_effect=AssertionError('advisory published')):
            mod.observe_validation(summary)
            mod.observe_validation(summary)


if __name__ == '__main__':
    unittest.main()
