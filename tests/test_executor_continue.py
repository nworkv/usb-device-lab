"""Executor exits are findings, not campaign termination, in both transports."""
import json
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path
import sys
import threading
import unittest
try:
    from . import test_dual_campaign as fixtures
except ImportError:
    import test_dual_campaign as fixtures

EngineTests = fixtures.EngineTests

class ExecutorContinueTests(unittest.TestCase):
    setUp = EngineTests.setUp
    def run_mode(self, mode, **kwargs):
        original = fixtures.config
        with patch.object(fixtures, 'config', side_effect=lambda root, selected: replace(original(root, selected), seconds=0.5)):
            return EngineTests.run_mode(self, mode, **kwargs)

    def test_early_exit_keeps_next_attempt_and_checkpoint(self):
        for mode in ('uart', 'ssh'):
            for code in (0, 1, 2):
                with self.subTest(mode=mode, code=code):
                    calls = []
                    def command(path):
                        calls.append(path)
                        script = 'raise SystemExit(%d)' % code if len(calls) == 1 else 'import time; time.sleep(60)'
                        return [sys.executable, '-c', script]
                    result = self.run_mode(mode, command_factory=command)
                    first, second = result['outcomes']
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(first['executor_returncode'], code)
                    self.assertIsNone(first['infrastructure_error'])
                    self.assertIn('Executor exited before window completion', first['executor_error'])
                    self.assertFalse(first['window_completed'])
                    self.assertTrue(second['window_completed'])
                    self.assertIsNone(second['executor_error'])
                    root = Path(result['directory'])
                    self.assertTrue((root/'attempt-000000'/'config.json').is_file())
                    self.assertTrue((root/'attempt-000000'/'executor.log').is_file())
                    self.assertEqual(json.loads((root/'checkpoint.json').read_text())['next_iteration'], 2)

    def test_operator_stop_still_wins(self):
        stop = threading.Event()
        calls = []
        def command(path):
            calls.append(path)
            stop.set()
            return [sys.executable, '-c', 'import time; time.sleep(60)']
        result = self.run_mode('uart', stop=stop, command_factory=command)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result['outcomes'][0]['interrupted'])
        self.assertIsNone(result['outcomes'][0]['executor_error'])

    def test_command_factory_errors_remain_fatal(self):
        def command(path):
            raise RuntimeError('command construction failed')
        with self.assertRaisesRegex(RuntimeError, 'command construction failed'):
            self.run_mode('uart', command_factory=command)

if __name__ == '__main__':
    unittest.main()
