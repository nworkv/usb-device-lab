"""Run the cleanup gate in subprocesses to verify its exit codes and reports."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_rpc_cleanup.py'

class CleanupGateTests(unittest.TestCase):
    def run_gate(self, source='', missing=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'tests'
            if not missing:
                tests.mkdir()
                (tests / 'test_rpc_probe.py').write_text(source, encoding='utf-8')
            report = root / 'reports/result.json'
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--tests-dir', str(tests), '--report', str(report)],
                capture_output=True, text=True, timeout=15)
            self.assertTrue(report.is_file(), completed.stderr)
            return completed.returncode, json.loads(report.read_text(encoding='utf-8'))

    def test_success(self):
        status, report = self.run_gate('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        self.assertEqual(report['tests_run'], 1)
        self.assertIsNone(report['infrastructure_error'])

    def test_failure(self):
        status, report = self.run_gate('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail("probe")\n')
        self.assertEqual(status, 1)
        self.assertFalse(report['passed'])
        self.assertEqual(report['failures'], 1)

    def test_resource_warning_fails_gate(self):
        status, report = self.run_gate('import unittest,warnings\nclass T(unittest.TestCase):\n def test_leak(self): warnings.warn("probe leak",ResourceWarning)\n')
        self.assertEqual(status, 1)
        self.assertEqual(report['resource_warnings'][0]['message'], 'probe leak')

    def test_empty_suite_fails_gate(self):
        status, report = self.run_gate()
        self.assertEqual(status, 1)
        self.assertEqual(report['tests_run'], 0)
        self.assertFalse(report['passed'])

    def test_all_skipped_fails_and_records_reason(self):
        status, report = self.run_gate('import unittest\nclass T(unittest.TestCase):\n @unittest.skip("probe skip")\n def test_skip(self): pass\n')
        self.assertEqual(status, 1)
        self.assertEqual(report['skipped'][0]['reason'], 'probe skip')

    def test_import_error_fails_gate(self):
        status, report = self.run_gate('raise RuntimeError("probe import")\n')
        self.assertEqual(status, 1)
        self.assertEqual(report['errors'], 1)

    def test_missing_directory_still_writes_report(self):
        status, report = self.run_gate(missing=True)
        self.assertEqual(status, 1)
        self.assertFalse(report['passed'])
        self.assertIsNotNone(report['infrastructure_error'])
        self.assertEqual(report['tests_run'], 0)

    def test_unexpected_success_fails_gate(self):
        status, report = self.run_gate('import unittest\nclass T(unittest.TestCase):\n @unittest.expectedFailure\n def test_unexpected(self): pass\n')
        self.assertEqual(status, 1)
        self.assertEqual(report['unexpected_successes'], 1)

if __name__ == '__main__':
    unittest.main()
