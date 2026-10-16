"""Skip controls use the existing fake-device/receiver lifecycle fixtures."""
import json
from pathlib import Path
import sys
import threading
import unittest

try:
    from .test_dual_campaign import EngineTests
except ImportError:
    from test_dual_campaign import EngineTests


class SkipTests(unittest.TestCase):
    setUp = EngineTests.setUp
    run_mode = EngineTests.run_mode

    def test_pending_skip_does_not_launch_first_executor(self):
        for mode in ('uart', 'ssh'):
            skip = threading.Event()
            skip.set()
            calls = []
            def command(path):
                calls.append(path)
                return [sys.executable, '-c', 'import time; time.sleep(60)']
            result = self.run_mode(mode, skip=skip, command_factory=command)
            first, second = result['outcomes']
            self.assertTrue(first['skipped'])
            self.assertFalse(first['window_completed'])
            self.assertIsNone(first['executor_returncode'])
            self.assertFalse(first['interrupted'])
            self.assertFalse(second['skipped'])
            self.assertTrue(second['window_completed'])
            self.assertEqual(len(calls), 1)
            self.assertFalse(skip.is_set())
            checkpoint = Path(result['directory']) / 'checkpoint.json'
            self.assertEqual(json.loads(checkpoint.read_text())['next_iteration'], 2)

    def test_skip_terminates_executor_and_continues(self):
        for mode in ('uart', 'ssh'):
            skip = threading.Event()
            calls = []
            def command(path):
                calls.append(path)
                if len(calls) == 1:
                    skip.set()
                return [sys.executable, '-c', 'import time; time.sleep(60)']
            result = self.run_mode(mode, skip=skip, command_factory=command)
            first, second = result['outcomes']
            self.assertTrue(first['skipped'])
            self.assertIn(first['executor_returncode'], (-15, -9))
            self.assertIsNone(first['infrastructure_error'])
            self.assertTrue(second['window_completed'])
            self.assertEqual(len(calls), 2)

    def test_stop_takes_precedence_and_does_not_advance_checkpoint(self):
        for mode in ('uart', 'ssh'):
            skip, stop = threading.Event(), threading.Event()
            def command(path):
                skip.set()
                stop.set()
                return [sys.executable, '-c', 'import time; time.sleep(60)']
            result = self.run_mode(mode, skip=skip, stop=stop, command_factory=command)
            self.assertEqual(len(result['outcomes']), 1)
            self.assertTrue(result['outcomes'][0]['interrupted'])
            self.assertFalse(result['outcomes'][0]['skipped'])
            checkpoint = Path(result['directory']) / 'checkpoint.json'
            self.assertEqual(json.loads(checkpoint.read_text())['next_iteration'], 0)

    def test_skip_does_not_hide_command_factory_failure(self):
        for mode in ('uart', 'ssh'):
            skip = threading.Event()
            def command(path):
                skip.set()
                raise RuntimeError('fixture command failure')
            with self.assertRaisesRegex(RuntimeError, 'fixture command failure'):
                self.run_mode(mode, skip=skip, command_factory=command)


if __name__ == '__main__':
    unittest.main()
