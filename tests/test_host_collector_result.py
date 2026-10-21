"""Preserve collector diagnostics without accepting failed-run coverage."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from usb_device_lab import host_agent

class CollectorResultTests(unittest.TestCase):
    def run_agent(self, failure=None, trailing=''):
        reply = {'pcs': ['0x2', '0x1', '0x1'], 'saturated': False,
                 'coverage_valid': True, 'collector_note': 'saved reply'}
        child = Mock()
        child.poll.return_value = 0
        child.returncode = 7 if failure == 'exit' else 0
        if failure == 'stdin':
            child.stdin.close.side_effect = OSError('stdin close failed')
        reader = Mock()
        reader.receive.side_effect = [{'ready': True}, {'started': True}, reply]
        logs = Mock()
        logs.close.return_value = {'kernel_log': 'saved kernel evidence', 'log_dropped': 0}
        def cleanup(process, rpc=None):
            return ['RPC close: reader failed'] if failure == 'streams' and process is child else []
        output = io.StringIO()
        commands = '{"command":"start"}\n{"command":"stop"}\n' + trailing
        with patch.object(sys, 'argv', ['agent', '--bus', '1', '--collector', 'unused']), \
             patch.object(sys, 'stdin', io.StringIO(commands)), \
             patch.object(host_agent, 'KernelLogs', return_value=logs), \
             patch.object(host_agent, 'Lines', return_value=reader), \
             patch.object(host_agent.subprocess, 'Popen', return_value=child), \
             patch.object(host_agent, 'close_rpc_streams', side_effect=cleanup), \
             redirect_stdout(output):
            status = host_agent.main()
        return status, [json.loads(line) for line in output.getvalue().splitlines()]

    def assert_preserved(self, failure):
        status, replies = self.run_agent(failure)
        self.assertEqual(status, 1)
        error = replies[-1]
        self.assertFalse(error['coverage_valid'])
        self.assertEqual(error['pcs'], [])
        self.assertEqual(error['collector_result']['pcs'], ['0x2', '0x1', '0x1'])
        self.assertEqual(error['collector_result']['collector_note'], 'saved reply')
        self.assertEqual(error['kernel_log'], 'saved kernel evidence')
        self.assertNotIn('kernel_log', error['collector_result'])

    def test_preserves_reply_after_stream_cleanup_failure(self):
        self.assert_preserved('streams')

    def test_preserves_reply_after_nonzero_collector_exit(self):
        self.assert_preserved('exit')

    def test_preserves_reply_after_stdin_close_failure(self):
        self.assert_preserved('stdin')

    def test_success_response_stays_flat_and_normalized(self):
        status, replies = self.run_agent()
        self.assertEqual(status, 0)
        self.assertTrue(replies[-1]['coverage_valid'])
        self.assertEqual(replies[-1]['pcs'], ['0x1', '0x2'])
        self.assertNotIn('collector_result', replies[-1])

    def test_later_protocol_error_does_not_reuse_successful_reply(self):
        status, replies = self.run_agent(trailing='{"command":"invalid"}\n')
        self.assertEqual(status, 1)
        self.assertEqual(replies[-1]['error'], 'invalid agent state')
        self.assertNotIn('collector_result', replies[-1])

if __name__ == '__main__':
    unittest.main()
