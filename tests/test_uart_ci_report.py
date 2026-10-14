import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / 'scripts' / 'run_uart_tests.py'
spec = importlib.util.spec_from_file_location('uart_ci_report_runner', RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class UARTCIReportTests(unittest.TestCase):
    def report(self, case):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            code = runner.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(case), path, io.StringIO())
            return code, json.loads(path.read_text())

    def test_success_report(self):
        class Passing(unittest.TestCase):
            def test_ok(self):
                self.assertEqual(1, 1)
        code, data = self.report(Passing)
        self.assertEqual(code, 0)
        self.assertTrue(data['successful'])
        self.assertEqual(data['records'][0]['status'], 'passed')

    def test_failure_and_error_exit_nonzero(self):
        class Broken(unittest.TestCase):
            def test_fail(self):
                self.fail('fixture failure')
            def test_error(self):
                raise ValueError('fixture error')
        code, data = self.report(Broken)
        self.assertEqual(code, 1)
        self.assertEqual((data['failures'], data['errors']), (1, 1))
        self.assertEqual({r['status'] for r in data['records']}, {'failed','error'})
        self.assertTrue(all(r['details'] for r in data['records']))

    def test_subtest_failure_not_reported_as_success(self):
        class Subtests(unittest.TestCase):
            def test_sub(self):
                for value in (0, 1):
                    with self.subTest(value=value):
                        self.assertEqual(value, 0)
        code, data = self.report(Subtests)
        self.assertEqual(code, 1)
        self.assertEqual(data['records'][0]['status'], 'failed_subtest')

    def test_skip_and_expected_failure_are_explicit(self):
        class Cases(unittest.TestCase):
            @unittest.skip('fixture skip')
            def test_skip(self):
                pass
            @unittest.expectedFailure
            def test_expected(self):
                self.fail('expected fixture failure')
        code, data = self.report(Cases)
        self.assertEqual(code, 0)
        self.assertEqual({r['status'] for r in data['records']}, {'skipped','expected_failure'})
        self.assertEqual((data['skipped'], data['expected_failures']), (1,1))

    def test_unexpected_success_is_failure(self):
        class Case(unittest.TestCase):
            @unittest.expectedFailure
            def test_unexpected(self):
                pass
        code, data = self.report(Case)
        self.assertEqual(code, 1)
        self.assertEqual(data['records'][0]['status'], 'unexpected_success')

    def test_no_tests_is_not_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            code = runner.run_suite(unittest.TestSuite(), path, io.StringIO())
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(path.read_text())['successful'])

if __name__ == '__main__':
    unittest.main()
