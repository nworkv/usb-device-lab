"""Tests for the public per-iteration JSON summary."""
import json
import unittest
from usb_device_lab.runner import run_summary


class RunSummaryTests(unittest.TestCase):
    def test_kernel_candidate_is_not_reported_as_confirmed(self):
        result = {
            'errors': [{'kind': 'kernel', 'summary': 'KASAN: test'}],
            'kernel_events': [{'kind': 'kasan'}],
            'coverage_valid': True,
            'verdict': {'outcome': 'kernel_candidate', 'confirmation': 'unconfirmed', 'telemetry_complete': True},
        }
        summary = run_summary('run-test', result, 2)
        self.assertEqual(summary['run'], 'run-test')
        self.assertEqual(summary['new_pcs'], 2)
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(summary['outcome'], 'kernel_candidate')
        self.assertEqual(summary['kernel_event_count'], 1)
        self.assertEqual(summary['confirmation'], 'unconfirmed')
        self.assertEqual(json.loads(json.dumps(summary)), summary)

    def test_no_change_is_preserved(self):
        summary = run_summary('run-test', {'coverage_valid': True, 'verdict': {'outcome': 'no_change', 'telemetry_complete': True}}, 0)
        self.assertEqual(summary['outcome'], 'no_change')
        self.assertEqual(summary['kernel_event_count'], 0)
        self.assertEqual(summary['errors'], 0)
        self.assertTrue(summary['coverage_valid'])

    def test_missing_verdict_is_unknown_not_success(self):
        summary = run_summary('run-test', {}, 0)
        self.assertEqual(summary['outcome'], 'inconclusive')
        self.assertEqual(summary['confirmation'], 'unknown')
        self.assertFalse(summary['telemetry_complete'])
        self.assertFalse(summary['coverage_valid'])


if __name__ == '__main__':
    unittest.main()
