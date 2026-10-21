"""Host-agent integration with real collector pipes and a fake kernel reader."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from usb_device_lab import host_agent

COLLECTOR = r"""import json, sys
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    if line == 'start\n':
        print(json.dumps({'started': True}), flush=True)
    elif line == 'stop\n':
        print(json.dumps({'pcs': ['0x2', '0x1', '0x1'], 'saturated': False}), flush=True)
"""

class Logs:
    def close(self):
        return {'kernel_log': 'saved kernel evidence', 'log_dropped': 0}

class HostCollectorCleanupTests(unittest.TestCase):
    def run_agent(self, commands, collector=COLLECTOR):
        children = []
        popen = subprocess.Popen
        def track(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'collector'
            script.write_text('#!' + sys.executable + '\n' + collector)
            script.chmod(0o700)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['agent', '--bus', '1', '--collector', str(script)]), \
                 patch.object(sys, 'stdin', io.StringIO(commands)), \
                 patch.object(host_agent, 'KernelLogs', Logs), \
                 patch.object(host_agent.subprocess, 'Popen', side_effect=track), \
                 redirect_stdout(output):
                status = host_agent.main()
        for child in children:
            self.assertIsNotNone(child.poll())
            self.assertTrue(child.stdin.closed)
            self.assertTrue(child.stdout.closed)
        return status, [json.loads(line) for line in output.getvalue().splitlines()], children

    def test_two_start_stop_cycles_close_both_collectors(self):
        status, replies, children = self.run_agent(
            '{"command":"start"}\n{"command":"stop"}\n' * 2)
        self.assertEqual(status, 0)
        self.assertEqual(len(children), 2)
        self.assertEqual(len(replies), 5)
        for reply in (replies[2], replies[4]):
            self.assertTrue(reply['coverage_valid'])
            self.assertEqual(reply['pcs'], ['0x1', '0x2'])
            self.assertEqual(reply['kernel_log'], 'saved kernel evidence')

    def test_collector_protocol_failure_closes_pipes_and_keeps_evidence(self):
        status, replies, children = self.run_agent(
            '{"command":"start"}\n', "print('[]', flush=True)\n")
        self.assertEqual(status, 1)
        self.assertEqual(len(children), 1)
        self.assertFalse(replies[-1]['coverage_valid'])
        self.assertIn('protocol object required', replies[-1]['error'])
        self.assertEqual(replies[-1]['kernel_log'], 'saved kernel evidence')

    def test_input_eof_reaps_active_collector_and_closes_pipes(self):
        status, replies, children = self.run_agent('{"command":"start"}\n')
        self.assertEqual(status, 0)
        self.assertEqual(len(children), 1)
        self.assertTrue(replies[-1]['started'])

if __name__ == '__main__':
    unittest.main()
