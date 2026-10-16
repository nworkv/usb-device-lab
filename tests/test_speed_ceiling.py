"""Reject known speed-ceiling conflicts before opening Raw Gadget."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from usb_device_lab import device as executor
from usb_device_lab.usb_speeds import check_requested_speed


class SpeedCeilingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.udc = self.root / 'class/udc/fixture.udc'
        self.udc.mkdir(parents=True)
        self.data = dict(udc_device='fixture.udc', speed=3)

    def maximum(self, label):
        (self.udc / 'maximum_speed').write_text(label, encoding='ascii')

    def test_requested_high_above_full_is_rejected_without_fallback(self):
        self.maximum('full-speed')
        with self.assertRaisesRegex(ValueError, 'exceeds UDC maximum'):
            check_requested_speed(self.data, self.root)
        self.assertEqual(self.data['speed'], 3)

    def test_within_ceiling_is_not_hardware_compatibility(self):
        self.maximum('high-speed')
        self.data['speed'] = 1
        result = check_requested_speed(self.data, self.root)
        self.assertTrue(result['not_above_reported_maximum'])
        self.assertFalse(result['controller_support_confirmed'])
        self.assertFalse(result['hardware_checked'])
        self.assertEqual(self.data['speed'], 1)

    def test_missing_maximum_remains_explicitly_unknown(self):
        result = check_requested_speed(self.data, self.root)
        self.assertIsNone(result['not_above_reported_maximum'])
        self.assertEqual(result['maximum']['error'], 'missing')
        self.assertFalse(result['controller_support_confirmed'])

    def test_unsupported_speed_rejected_before_sysfs_read(self):
        for speed in (0,4,5,6,True,'3'):
            with self.subTest(speed=speed),patch('usb_device_lab.usb_speeds.read_speed_attribute') as read:
                with self.assertRaises(ValueError):
                    check_requested_speed(dict(self.data, speed=speed), self.root)
                read.assert_not_called()

    def test_udc_path_validation(self):
        for udc in ('', '.', '..', '../escape', '/tmp/controller', 'a/b', None):
            with self.subTest(udc=udc),self.assertRaises(ValueError):
                check_requested_speed(dict(self.data, udc_device=udc), self.root)

    def test_gate_runs_before_raw_io_and_closes_protocol(self):
        self.maximum('full-speed')
        device = executor.Device.__new__(executor.Device)
        device.data, device.io, device.protocol = self.data, None, Mock()
        device.disable = Mock()
        with patch.object(executor, 'check_requested_speed', side_effect=lambda data: check_requested_speed(data, self.root)),patch.object(executor, 'RawIO') as raw,patch.object(executor.signal, 'signal') as signal:
            with self.assertRaisesRegex(ValueError, 'exceeds UDC maximum'):
                device.run()
        raw.assert_not_called()
        signal.assert_not_called()
        device.protocol.close.assert_called_once_with()
        device.disable.assert_not_called()

    def test_gate_closes_already_owned_io_without_usb_calls(self):
        self.maximum('full-speed')
        device = executor.Device.__new__(executor.Device)
        device.data, device.io, device.protocol = self.data, Mock(), Mock()
        device.disable = Mock()
        with patch.object(executor, 'check_requested_speed', side_effect=lambda data: check_requested_speed(data, self.root)):
            with self.assertRaises(ValueError):
                device.run()
        device.io.call.assert_not_called()
        device.io.close.assert_called_once_with()
        device.protocol.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
