"""Real collector pipes preserve stop diagnostics across injected cleanup failures."""
import io
import json
import subprocess
import sys
import tempfile
import threading
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
        print('collector stderr evidence', file=sys.stderr, flush=True)
        print(json.dumps({'pcs': ['0x2', '0x1'], 'saturated': False,
                          'collector_note': 'real pipe reply'}), flush=True)
"""

class Logs:
    def close(self):
        return {'kernel_log': 'kernel evidence', 'log_dropped': 0}

class RealCollectorResultTests(unittest.TestCase):
    def run_agent(self, failure):
        children, readers = [], []
        popen, lines, cleanup = subprocess.Popen, host_agent.Lines, host_agent.close_rpc_streams
        def track_process(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child
        def track_reader(child):
            reader = lines(child)
            readers.append(reader)
            return reader
        def fail_after_close(child, reader=None):
            errors = cleanup(child, reader)
            if failure == 'cleanup' and child is not None:
                errors.append('injected cleanup failure')
            return errors
        def final_cleanup():
            for child in children:
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=3)
                finally:
                    reader = next((r for r in readers if r.process is child), None)
                    cleanup(child, reader)
        self.addCleanup(final_cleanup)
        collector = COLLECTOR
        if failure == 'exit':
            collector += '\nraise SystemExit(7)\n'
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'collector'
            script.write_text('#!' + sys.executable + '\n' + collector)
            script.chmod(0o700)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['agent', '--bus', '1', '--collector', str(script)]), \
                 patch.object(sys, 'stdin', io.StringIO('{"command":"start"}\n{"command":"stop"}\n')), \
                 patch.object(host_agent, 'KernelLogs', Logs), \
                 patch.object(host_agent, 'Lines', side_effect=track_reader), \
                 patch.object(host_agent.subprocess, 'Popen', side_effect=track_process), \
                 patch.object(host_agent, 'close_rpc_streams', side_effect=fail_after_close), \
                 redirect_stdout(output):
                status = host_agent.main()
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
        self.assertTrue(all(not reader.thread.is_alive() for reader in readers))
        return status, [json.loads(line) for line in output.getvalue().splitlines()]

    def assert_failure_evidence(self, failure):
        status, replies = self.run_agent(failure)
        self.assertEqual(status, 1)
        error = replies[-1]
        self.assertFalse(error['coverage_valid'])
        self.assertEqual(error['pcs'], [])
        self.assertEqual(error['collector_result']['collector_note'], 'real pipe reply')
        self.assertEqual(error['collector_result']['pcs'], ['0x2', '0x1'])
        self.assertEqual(error['kernel_log'], 'kernel evidence')
        self.assertIn('collector stderr evidence', error['collector_stderr'])

    def test_real_stop_reply_survives_cleanup_error(self):
        self.assert_failure_evidence('cleanup')

    def test_real_stop_reply_survives_exit_failure(self):
        self.assert_failure_evidence('exit')

    def test_real_stop_success_is_unchanged(self):
        status, replies = self.run_agent(None)
        self.assertEqual(status, 0)
        self.assertTrue(replies[-1]['coverage_valid'])
        self.assertEqual(replies[-1]['pcs'], ['0x1', '0x2'])
        self.assertNotIn('collector_result', replies[-1])

if __name__ == '__main__':
    unittest.main()
