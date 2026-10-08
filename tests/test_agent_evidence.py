"""Evidence preservation when the collector or protocol fails."""
import unittest
from unittest.mock import Mock
from usb_device_lab.host_agent import close_logs
from usb_device_lab.runner import apply_agent_reply


class AgentEvidenceTests(unittest.TestCase):
    def test_close_logs_preserves_normal_result(self):
        logs = Mock()
        logs.close.return_value = {'kernel_log': 'WARNING: test', 'log_dropped': 0}
        self.assertEqual(close_logs(logs), logs.close.return_value)

    def test_close_failure_preserves_available_records(self):
        logs = Mock()
        logs.records = ['BUG: KASAN: test\n']
        logs.lost = 0
        logs.close.side_effect = RuntimeError('reader did not stop')
        result = close_logs(logs)
        self.assertIn('KASAN:', result['kernel_log'])
        self.assertGreaterEqual(result['log_dropped'], 1)
        self.assertTrue(result['log_drain_incomplete'])

    def test_missing_reader_is_allowed(self):
        self.assertEqual(close_logs(None), {})

    def test_agent_error_preserves_kernel_log_and_stderr(self):
        original = {'kind': 'cleanup', 'summary': 'old diagnostic'}
        result = {'errors': [original]}
        reply = {'error': 'collector failed', 'namespace': 'boot:2',
                 'kernel_log': 'BUG: KASAN: test', 'collector_stderr': 'collector detail',
                 'pcs': [123], 'coverage_valid': True}
        with self.assertRaisesRegex(RuntimeError, 'collector failed'):
            apply_agent_reply(result, reply, namespace='boot:2')
        self.assertEqual(result['kernel_log'], reply['kernel_log'])
        self.assertEqual(result['collector_stderr'], 'collector detail')
        self.assertEqual(result['errors'], [original])
        self.assertEqual(result['pcs'], [])
        self.assertFalse(result['coverage_valid'])

    def test_namespace_mismatch_rejects_pcs_not_evidence(self):
        result = {'errors': []}
        with self.assertRaisesRegex(RuntimeError, 'namespace changed'):
            apply_agent_reply(result, {'namespace': 'other:2', 'pcs': [123],
                'coverage_valid': True, 'kernel_log': 'WARNING: test'}, namespace='boot:2')
        self.assertEqual(result['kernel_log'], 'WARNING: test')
        self.assertEqual(result['pcs'], [])
        self.assertFalse(result['coverage_valid'])

    def test_successful_reply_preserves_coverage(self):
        result = {'errors': []}
        reply = {'namespace': 'boot:2', 'pcs': [123], 'coverage_valid': True}
        self.assertIs(apply_agent_reply(result, reply, namespace='boot:2'), reply)
        self.assertEqual(result['pcs'], [123])
        self.assertTrue(result['coverage_valid'])

    def test_invalid_reply_does_not_destroy_result(self):
        result = {'errors': []}
        with self.assertRaisesRegex(RuntimeError, 'invalid host agent reply'):
            apply_agent_reply(result, [])
        self.assertEqual(result, {'errors': []})


if __name__ == '__main__':
    unittest.main()
