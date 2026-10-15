"""UDC identity regression: no real controller, SSH server or tty is opened."""
import io
import json
import os
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.dual_preflight import inspect, udc_driver_name
from usb_device_lab import dual_init


class UDCIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sys = self.root / 'sys'
        self.udc = self.sys / 'class/udc/fe980000.usb'
        (self.udc / 'device').mkdir(parents=True)
        self.linux_driver = self.sys / 'bus/platform/drivers/dwc2'
        self.linux_driver.mkdir(parents=True)
        (self.udc / 'device/driver').symlink_to(self.linux_driver, target_is_directory=True)
        (self.udc / 'uevent').write_text('USB_UDC_NAME=fe980000.usb\n')
        self.seed = self.root / 'seed.json'; self.seed.write_text('{}')
        for name in ('key', 'known_hosts'):
            path = self.root / name; path.write_text('fixture'); path.chmod(0o600)
        self.config = SimpleNamespace(telemetry_mode='ssh', udc='fe980000.usb',
            gadget_udc_driver='fe980000.usb', results_dir=self.root/'results',
            logs_dir=self.root/'logs', corpus_out=self.root/'corpus',
            ssh=SimpleNamespace(identity_file=str(self.root/'key'), known_hosts_file=str(self.root/'known_hosts')),
            uart=SimpleNamespace(port='/dev/null'))

    def report(self):
        with patch('usb_device_lab.dual_preflight.shutil.which', return_value='/usr/bin/ssh'),              patch('usb_device_lab.uart_receiver.check_settings'):
            return inspect(self.config, [str(self.seed)], sys_root=self.sys,
                           raw_path=Path('/dev/null'), lock_root=self.root/'locks')

    def driver_check(self, report):
        return next(c for c in report['checks'] if c['name'] == 'udc_driver_matches')

    def test_api_name_is_not_linux_driver_basename(self):
        self.assertEqual(udc_driver_name(self.udc), 'fe980000.usb')
        self.assertEqual((self.udc/'device/driver').resolve().name, 'dwc2')

    def test_correct_identity_passes_in_both_modes(self):
        for mode in ('uart', 'ssh'):
            self.config.telemetry_mode = mode
            report = self.report(); check = self.driver_check(report)
            self.assertTrue(check['passed']); self.assertTrue(report['metadata_checks_passed'])
            self.assertEqual(check['details']['configured'], 'fe980000.usb')
            self.assertEqual(check['details']['observed'], 'fe980000.usb')
            self.assertEqual(check['details']['linux_driver'], 'dwc2')
            self.assertEqual(check['details']['source'], 'USB_UDC_NAME')

    def test_linux_driver_in_config_is_rejected(self):
        self.config.gadget_udc_driver = 'dwc2'
        report = self.report()
        self.assertFalse(self.driver_check(report)['passed'])
        self.assertFalse(report['metadata_checks_passed'])

    def test_missing_identity_does_not_fall_back_to_dwc2(self):
        (self.udc/'uevent').write_text('OTHER=value\n')
        check = self.driver_check(self.report())
        self.assertFalse(check['passed']); self.assertIsNone(check['details']['observed'])
        self.assertIsNotNone(check['details']['identity_error'])

    def test_invalid_duplicate_and_oversized_identity_rejected(self):
        for value in ('USB_UDC_NAME=\n', 'USB_UDC_NAME=../bad\n',
                      'USB_UDC_NAME=a\nUSB_UDC_NAME=b\n', 'x'*4097):
            (self.udc/'uevent').write_text(value)
            with self.assertRaises(ValueError): udc_driver_name(self.udc)

    def test_unreadable_missing_uevent_is_reported(self):
        (self.udc/'uevent').unlink()
        check = self.driver_check(self.report())
        self.assertFalse(check['passed']); self.assertIsNotNone(check['details']['identity_error'])

    def test_generator_uses_uevent_not_linux_driver(self):
        config = self.root/'generated.toml'
        real_path = Path
        def paths(value):
            return self.sys/'class/udc' if value == '/sys/class/udc' else real_path(value)
        with patch.object(dual_init, 'Path', side_effect=paths),              patch.object(dual_init, 'from_dict'), redirect_stdout(io.StringIO()):
            self.assertEqual(dual_init.main(['--repo-dir', str(self.root/'repo'), '--state-dir', str(self.root/'state'),
                '--config', str(config), '--mode', 'ssh', '--ssh-host', '10.10.10.208', '--ssh-user', 'nikita',
                '--ssh-key', str(self.root/'key'), '--known-hosts', str(self.root/'known_hosts')]), 0)
        value = tomllib.loads(config.read_text())
        self.assertEqual(value['gadget']['udc'], 'fe980000.usb')
        self.assertEqual(value['gadget']['udc_driver'], 'fe980000.usb')
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)

if __name__ == '__main__': unittest.main()
