"""Regression tests for the kernel-evidence classification contract."""
import copy
import json
import unittest
from usb_device_lab.errors import classify, kernel_events


def run_result(**changes):
    result = {
        'coverage_valid': True, 'pcs': [4096, 8192], 'errors': [],
        'kernel_log': '', 'executor_log': '',
        'executor_returncode': 0, 'window_completed': False,
    }
    result.update(changes)
    return classify(result)


class TriageErrorsTests(unittest.TestCase):
    def test_normal_usb_log_is_not_a_finding(self):
        result = run_result(kernel_log='6,12,100,-;usb 2-1: new full-speed USB device\n')
        self.assertEqual(result['verdict']['outcome'], 'coverage_observed')
        self.assertFalse(result['verdict']['kernel_evidence_detected'])
        self.assertEqual(result['errors'], [])

    def test_kernel_signal_kinds(self):
        headers = {
            'panic': 'Kernel panic - not syncing: test',
            'kasan': 'BUG: KASAN: slab-out-of-bounds in usb_test',
            'kfence': 'BUG: KFENCE: use-after-free read in usb_test',
            'kmsan': 'BUG: KMSAN: uninit-value in usb_test',
            'ubsan': 'UBSAN: array-index-out-of-bounds in usb_test',
            'kcsan': 'BUG: KCSAN: data-race in usb_test',
            'oops': 'Oops: 0000 [#1] SMP',
            'fault': 'general protection fault, probably for non-canonical address',
            'bug': 'BUG: kernel test failure',
            'warning': 'WARNING: CPU: 0 PID: 42 at usb_test',
            'hung_task': 'INFO: task usb_worker:42 blocked for more than 120 seconds.',
        }
        for kind, header in headers.items():
            with self.subTest(kind=kind):
                result = run_result(kernel_log='3,123,456,-;' + header)
                self.assertEqual(result['kernel_events'][0]['kind'], kind)
                self.assertEqual(result['verdict']['outcome'], 'kernel_candidate')
                self.assertEqual(result['verdict']['confirmation'], 'unconfirmed')

    def test_context_and_raw_header_are_preserved(self):
        log = 'before\nWARNING: CPU: 0 at usb_test+0x10/0x80\nCall Trace:\n usb_worker+0x20/0x90\n'
        event = kernel_events(log)[0]
        self.assertEqual(event['line_number'], 2)
        self.assertIn('usb_test+0x10/0x80', event['raw_summary'])
        self.assertIn('Call Trace:', event['context'])
        self.assertEqual(len(event['header_fingerprint']), 64)

    def test_executor_failure_is_not_kernel_evidence(self):
        result = run_result(executor_returncode=1)
        self.assertEqual(result['verdict']['outcome'], 'executor_failure')
        self.assertFalse(result['verdict']['kernel_evidence_detected'])

    def test_executor_io_diagnostic(self):
        result = run_result(executor_log='{"kind": "protocol_error", "detail": "test"}\n')
        self.assertEqual(result['verdict']['outcome'], 'executor_failure')
        self.assertIn('executor_io', result['verdict']['diagnostic_kinds'])

    def test_timer_termination_is_expected(self):
        for code in (-15, -9):
            with self.subTest(code=code):
                result = run_result(executor_returncode=code, window_completed=True)
                self.assertEqual(result['verdict']['outcome'], 'coverage_observed')
                self.assertEqual(result['errors'], [])

    def test_early_signal_is_executor_failure(self):
        for code in (-15, -9):
            with self.subTest(code=code):
                result = run_result(executor_returncode=code, window_completed=False)
                self.assertEqual(result['verdict']['outcome'], 'executor_failure')

    def test_infrastructure_error_keeps_legacy_diagnostic(self):
        error = {'kind': 'infrastructure', 'summary': 'SSH connection lost'}
        result = run_result(errors=[error], pcs=[], coverage_valid=False)
        self.assertIn(error, result['errors'])
        self.assertEqual(result['verdict']['outcome'], 'infrastructure_failure')
        self.assertFalse(result['verdict']['telemetry_complete'])
        self.assertNotIn('coverage_unavailable', result['verdict']['diagnostic_kinds'])

    def test_empty_coverage_is_not_success(self):
        result = run_result(pcs=[])
        self.assertFalse(result['coverage_valid'])
        self.assertEqual(result['verdict']['outcome'], 'infrastructure_failure')
        self.assertIn('coverage_unavailable', result['verdict']['diagnostic_kinds'])

    def test_saturated_coverage_is_rejected(self):
        result = run_result(saturated=True)
        self.assertFalse(result['coverage_valid'])
        self.assertIn('coverage', result['verdict']['diagnostic_kinds'])
        self.assertNotIn('coverage_unavailable', result['verdict']['diagnostic_kinds'])

    def test_kernel_evidence_survives_incomplete_telemetry(self):
        result = run_result(kernel_log='BUG: KASAN: test', log_dropped=2)
        self.assertEqual(result['verdict']['outcome'], 'kernel_candidate')
        self.assertFalse(result['verdict']['telemetry_complete'])
        self.assertEqual(result['verdict']['confirmation'], 'unconfirmed')

    def test_reclassification_is_idempotent_and_json_serializable(self):
        result = run_result(kernel_log='WARNING: CPU: 0 at usb_test')
        before = copy.deepcopy(result)
        self.assertIs(classify(result), result)
        self.assertEqual(result, before)
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == '__main__':
    unittest.main()
