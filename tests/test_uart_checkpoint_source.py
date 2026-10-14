import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'check_uart_checkpoint_source.py'
spec = importlib.util.spec_from_file_location('uart_checkpoint_guard', PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

GOOD = """def run_campaign():
    mutator.random.setstate(restored['prng'])
    checkpoint.write(root / 'checkpoint.json', digest, first, mutator.random, ident)
    for iteration in range(first, config.iterations):
        save(directory / 'result.json', result)
        if not stop.is_set():
            healthy()
            checkpoint.write(root / 'checkpoint.json', digest, iteration + 1, mutator.random, ident)
"""

class UARTCheckpointSourceGuardTests(unittest.TestCase):
    def test_current_contract_accepted(self):
        self.assertEqual(guard.check(GOOD), [])

    def test_missing_progress_matches_transfer_regression(self):
        text = GOOD[:GOOD.index('        if not stop.is_set():')]
        self.assertTrue(any('advancement' in e for e in guard.check(text)))

    def test_initial_checkpoint_missing(self):
        text = GOOD.replace("    checkpoint.write(root / 'checkpoint.json', digest, first, mutator.random, ident)\n", '')
        self.assertTrue(any('Initial' in e for e in guard.check(text)))

    def test_prng_restore_missing(self):
        text = GOOD.replace("    mutator.random.setstate(restored['prng'])\n", '')
        self.assertTrue(any('restoration' in e for e in guard.check(text)))

    def test_wrong_counter_or_missing_stop_guard(self):
        for text in (GOOD.replace('iteration + 1', 'iteration'), GOOD.replace('if not stop.is_set():', 'if True:'), GOOD.replace('iteration + 1', 'iteration + True')):
            with self.subTest(text=text):
                self.assertTrue(any('advancement' in e for e in guard.check(text)))

    def test_no_function_or_syntax_error(self):
        self.assertTrue(guard.check('pass'))
        with self.assertRaises(SyntaxError):
            guard.check('def broken(')

if __name__ == '__main__':
    unittest.main()
