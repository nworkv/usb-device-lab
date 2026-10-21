"""Run focused cleanup tests and fail on parent-process ResourceWarning."""
import argparse
import gc
import json
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ('test_rpc*.py', 'test_host_agent_eof.py',
            'test_host_collector*.py', 'test_runner_pipe_cleanup.py')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests-dir', type=Path, default=ROOT / 'tests')
    parser.add_argument('--report', type=Path, default=ROOT / 'build/rpc-cleanup-report.json')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    gc.collect()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always', ResourceWarning)
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for pattern in PATTERNS:
            suite.addTests(loader.discover(str(args.tests_dir), pattern=pattern))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        gc.collect()
    resources = [{'message': str(item.message), 'filename': item.filename,
                  'lineno': item.lineno} for item in captured
                 if issubclass(item.category, ResourceWarning)]
    passed = (result.wasSuccessful() and result.testsRun > len(result.skipped)
              and not resources)
    report = {
        'passed': passed,
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'unexpected_successes': len(result.unexpectedSuccesses),
        'expected_failures': len(result.expectedFailures),
        'skipped': [{'test': str(test), 'reason': reason}
                    for test, reason in result.skipped],
        'resource_warnings': resources,
        'patterns': list(PATTERNS),
        'scope': 'Focused software tests; parent-process ResourceWarning only. Not hardware validation.',
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
