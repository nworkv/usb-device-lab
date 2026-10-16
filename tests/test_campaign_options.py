"""Timing overrides preserve the immutable disk config and reject unsafe input."""
from dataclasses import dataclass
import math
import shutil
import subprocess
import unittest
from usb_device_lab.campaign_options import validate_options, apply_options, option_values, window_budget, SCRIPT


@dataclass(frozen=True)
class Config:
    iterations: int = 50
    seconds: float = 2.0
    pre_run_quiet_seconds: float = 0.5
    post_run_capture_seconds: float = 1.0
    udc: str = 'fixture.udc'


class CampaignOptionTests(unittest.TestCase):
    def raw(self):
        return dict(iterations=50, seconds=0.2, pre_run_quiet_seconds=0.1, post_run_capture_seconds=0.2)

    def test_override_does_not_modify_original(self):
        original = Config()
        value = apply_options(original, self.raw())
        self.assertEqual(original.seconds, 2.0)
        self.assertEqual(value.seconds, 0.2)
        self.assertEqual(value.udc, original.udc)
        self.assertIs(apply_options(original, {}), original)

    def test_budget_and_effective_values(self):
        values = validate_options(self.raw())
        self.assertAlmostEqual(window_budget(values), 25.0)
        self.assertEqual(option_values(Config(), values), values)

    def test_missing_and_extra_fields_rejected(self):
        for raw in ({}, dict(self.raw(), command='whoami'), [], None):
            with self.assertRaises(ValueError):
                validate_options(raw)
        raw = self.raw()
        del raw['seconds']
        with self.assertRaises(ValueError):
            validate_options(raw)

    def test_iteration_bounds_and_types(self):
        for value in (True, 0, -1, 10001, 1.5, '50'):
            with self.assertRaises(ValueError):
                validate_options(dict(self.raw(), iterations=value))
        self.assertEqual(validate_options(dict(self.raw(), iterations=10000))['iterations'], 10000)

    def test_nonfinite_and_boolean_timings_rejected(self):
        for field in ('seconds', 'pre_run_quiet_seconds', 'post_run_capture_seconds'):
            for value in (True, math.nan, math.inf, -math.inf, -1, '1'):
                raw = self.raw()
                raw[field] = value
                with self.assertRaises(ValueError):
                    validate_options(raw)

    def test_timing_boundaries(self):
        for field, value in (('seconds', 0), ('seconds', 301), ('pre_run_quiet_seconds', 31), ('post_run_capture_seconds', 31)):
            raw = self.raw()
            raw[field] = value
            with self.assertRaises(ValueError):
                validate_options(raw)
        raw = dict(iterations=1, seconds=300, pre_run_quiet_seconds=0, post_run_capture_seconds=30)
        self.assertEqual(validate_options(raw)['post_run_capture_seconds'], 30.0)

    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_script_syntax(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == '__main__':
    unittest.main()
