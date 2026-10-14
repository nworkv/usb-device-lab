"""UART unittest runner with an explicit machine-readable outcome; no environment dump."""
import argparse
import json
import platform
import sys
import time
import unittest
from pathlib import Path


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.current = {}

    def startTest(self, test):
        super().startTest(test)
        self.current[test] = dict(id=test.id(), status='running', started=time.monotonic(), details=[])

    def stopTest(self, test):
        record = self.current.pop(test)
        record['seconds'] = time.monotonic() - record.pop('started')
        self.records.append(record)
        super().stopTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.current[test]['status'] = 'passed'

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.current[test]['status'] = 'failed'
        self.current[test]['details'].append(self.failures[-1][1])

    def addError(self, test, error):
        super().addError(test, error)
        self.current[test]['status'] = 'error'
        self.current[test]['details'].append(self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.current[test]['status'] = 'skipped'
        self.current[test]['details'].append(reason)

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.current[test]['status'] = 'expected_failure'
        self.current[test]['details'].append(self.expectedFailures[-1][1])

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.current[test]['status'] = 'unexpected_success'

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.current[test]['status'] = 'failed_subtest'
            details = self.failures[-1][1] if issubclass(error[0], test.failureException) else self.errors[-1][1]
            self.current[test]['details'].append(dict(subtest=str(subtest), traceback=details))


def run_suite(suite, output, stream=sys.stderr):
    started = time.monotonic()
    result, aborted = None, None
    try:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    except BaseException as error:
        aborted = type(error).__name__ + ': ' + str(error)
    passed = result is not None and result.wasSuccessful() and result.testsRun > 0 and aborted is None
    report = dict(schema_version=1, python=platform.python_version(), platform=sys.platform,
        successful=passed, aborted=aborted, seconds=time.monotonic()-started,
        tests_run=result.testsRun if result else 0, records=result.records if result else [],
        failures=len(result.failures) if result else 0, errors=len(result.errors) if result else 0,
        skipped=len(result.skipped) if result else 0,
        expected_failures=len(result.expectedFailures) if result else 0,
        unexpected_successes=len(result.unexpectedSuccesses) if result else 0)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, allow_nan=False, indent=2)
        file.write('\n')
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run complete-checkout UART tests and write JSON results')
    parser.add_argument('--output', default='uart-ci-results/report.json')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    try:
        suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_uart*.py')
    except Exception as error:
        message = type(error).__name__ + ': ' + str(error)
        def discovery_failure():
            raise RuntimeError('UART test discovery failed: ' + message)
        suite = unittest.TestSuite([unittest.FunctionTestCase(discovery_failure)])
    return run_suite(suite, args.output)

if __name__ == '__main__':
    raise SystemExit(main())
