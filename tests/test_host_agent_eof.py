"""Host-agent EOF reports cleanup failures instead of returning success."""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from usb_device_lab import host_agent

class AgentEOFCleanupTests(unittest.TestCase):
    def run_agent(self, failure=None, commands='{"command":"start"}\n'):
        child = Mock()
        child.poll.return_value = None
        child.returncode = 0
        reader = Mock()
        reader.receive.side_effect = [{'ready': True}, {'started': True}]
        logs = Mock()
        logs.close.return_value = {'kernel_log': 'saved evidence', 'log_dropped': 0}
        errors = []
        if failure == 'kill':
            child.kill.side_effect = PermissionError('kill denied')
        elif failure == 'wait':
            child.wait.side_effect = subprocess.TimeoutExpired('collector', 3)
        elif failure == 'streams':
            errors = ['RPC close: reader alive']
        elif failure == 'logs':
            logs.close.side_effect = OSError('log close failed')
            logs.records = ['saved evidence']
            logs.lost = 0
        output = io.StringIO()
        with patch.object(sys, 'argv', ['agent', '--bus', '1', '--collector', 'unused']), \
             patch.object(sys, 'stdin', io.StringIO(commands)), \
             patch.object(host_agent, 'KernelLogs', return_value=logs), \
             patch.object(host_agent, 'Lines', return_value=reader), \
             patch.object(host_agent.subprocess, 'Popen', return_value=child), \
             patch.object(host_agent, 'close_rpc_streams', return_value=errors) as cleanup, \
             redirect_stdout(output):
            status = host_agent.main()
        cleanup.assert_called_once_with(child, reader)
        logs.close.assert_called_once_with()
        return status, [json.loads(line) for line in output.getvalue().splitlines()], child

    def test_clean_eof_keeps_existing_success_status(self):
        status, replies, child = self.run_agent()
        self.assertEqual(status, 0)
        self.assertEqual(len(replies), 2)
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=3)

    def test_kill_failure_is_reported(self):
        status, replies, child = self.run_agent('kill')
        self.assertEqual(status, 1)
        self.assertIn('kill denied', replies[-1]['collector_cleanup_error'])
        self.assertFalse(replies[-1]['coverage_valid'])
        self.assertEqual(replies[-1]['pcs'], [])
        self.assertEqual(replies[-1]['kernel_log'], 'saved evidence')

    def test_wait_timeout_is_reported(self):
        status, replies, child = self.run_agent('wait')
        self.assertEqual(status, 1)
        self.assertIn('collector stop:', replies[-1]['collector_cleanup_error'])
        self.assertEqual(replies[-1]['kernel_log'], 'saved evidence')

    def test_stream_failure_is_reported(self):
        status, replies, child = self.run_agent('streams')
        self.assertEqual(status, 1)
        self.assertEqual(replies[-1]['error'], 'collector cleanup failed')
        self.assertIn('reader alive', replies[-1]['collector_cleanup_error'])

    def test_original_error_and_cleanup_error_are_both_preserved(self):
        status, replies, child = self.run_agent(
            'streams', '{"command":"start"}\n{"command":"invalid"}\n')
        self.assertEqual(status, 1)
        self.assertEqual(replies[-1]['error'], 'invalid agent state')
        self.assertIn('reader alive', replies[-1]['collector_cleanup_error'])
        self.assertEqual(replies[-1]['kernel_log'], 'saved evidence')
        self.assertEqual(len(replies), 3)

    def test_log_evidence_survives_log_close_failure_on_error(self):
        status, replies, child = self.run_agent(
            'logs', '{"command":"start"}\n{"command":"invalid"}\n')
        self.assertEqual(status, 1)
        self.assertEqual(replies[-1]['kernel_log'], 'saved evidence')
        self.assertTrue(replies[-1]['log_drain_incomplete'])
        self.assertIn('log close failed', replies[-1]['log_read_error'])

if __name__ == '__main__':
    unittest.main()
