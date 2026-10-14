import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.uart_preflight import inspect

@unittest.skipUnless(os.name == 'posix', 'POSIX metadata required')
class UARTPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sys = self.root / 'sys'
        self.udc = self.sys / 'class/udc/test.udc'
        (self.udc / 'device').mkdir(parents=True)
        self.driver = self.sys / 'drivers/test-driver'
        self.driver.mkdir(parents=True)
        (self.udc / 'device/driver').symlink_to(self.driver)
        self.seed = self.root / 'seed.json'
        self.seed.write_text('{}')
        self.lock = self.root / 'locks'
        self.lock.mkdir(mode=0o700)
        self.config = SimpleNamespace(telemetry_mode='uart', udc='test.udc', gadget_udc_driver='test-driver',
            uart=SimpleNamespace(port='/dev/null'), results_dir=self.root / 'new/results', logs_dir=self.root / 'new/logs')

    def run_check(self):
        return inspect(self.config, [self.seed], sys_root=self.sys, raw_path='/dev/null', lock_root=self.lock)

    def test_metadata_pass_is_not_hardware_success(self):
        result = self.run_check()
        self.assertTrue(result['metadata_checks_passed'])
        self.assertFalse(result['hardware_checked'])
        self.assertFalse(result['runtime_connected'])
        self.assertIn('tty_identity', result['unknown'])
        self.assertFalse((self.root / 'new').exists())

    def test_regular_file_not_a_device(self):
        self.config.uart.port = str(self.seed)
        result = self.run_check()
        self.assertFalse(result['metadata_checks_passed'])
        self.assertFalse(next(c for c in result['checks'] if c['name']=='uart_character_node_readable')['passed'])

    def test_driver_mismatch_is_reported(self):
        self.config.gadget_udc_driver = 'test.udc'
        result = self.run_check()
        row = next(c for c in result['checks'] if c['name']=='udc_driver_matches')
        self.assertFalse(row['passed'])
        self.assertEqual(row['details']['observed'], 'test-driver')

    def test_missing_udc_and_seed(self):
        self.config.udc = 'absent'
        self.seed.unlink()
        result = self.run_check()
        self.assertFalse(result['metadata_checks_passed'])
        self.assertFalse(next(c for c in result['checks'] if c['name']=='udc_directory_present')['passed'])

    def test_cleanup_marker_blocks_metadata_pass(self):
        import hashlib
        path = self.lock / (hashlib.sha256(self.config.udc.encode()).hexdigest()+'.lock')
        path.write_text('{"unreaped_pid":123}')
        self.assertFalse(self.run_check()['metadata_checks_passed'])

    def test_no_device_open_process_or_directory_creation(self):
        with patch('os.open', side_effect=AssertionError('Device opened')), patch('subprocess.Popen', side_effect=AssertionError('Process launched')), patch.object(Path, 'mkdir', side_effect=AssertionError('Directory created')):
            result = self.run_check()
        self.assertTrue(result['metadata_checks_passed'])

    def test_udc_traversal_rejected(self):
        for name in ('..', '.', '../udc', '/absolute'):
            self.config.udc = name
            with self.assertRaises(ValueError):
                self.run_check()

    def test_unsafe_lock_directory(self):
        self.lock.chmod(0o777)
        self.assertFalse(self.run_check()['metadata_checks_passed'])

if __name__ == '__main__':
    unittest.main()
