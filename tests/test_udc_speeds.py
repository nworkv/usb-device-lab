"""UDC speed diagnostics read bounded files, never connect to USB or SSH."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from usb_device_lab.usb_speeds import SUPPORTED_MODEL_SPEEDS, read_speed_attribute, udc_speed_report


class UDCSpeedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name, value):
        (self.root / name).write_bytes(value)

    def test_report_distinguishes_maximum_and_current(self):
        self.write('maximum_speed', b'super-speed' + bytes((10,)))
        self.write('current_speed', b'high-speed' + bytes((10,)))
        report = udc_speed_report(self.root)
        self.assertEqual(report['maximum']['speed'], 5)
        self.assertEqual(report['current_at_inspection']['speed'], 3)
        self.assertIsNone(report['controller_supported_modes'])
        self.assertFalse(report['hardware_checked'])
        self.assertFalse(report['runtime_connected'])
        self.assertEqual([row['speed'] for row in report['software_supported_speeds']], [1, 2, 3])

    def test_all_known_labels(self):
        for label, expected in (('low-speed',1),('full-speed',2),('high-speed',3),('super-speed',5),('super-speed-plus',6)):
            with self.subTest(label=label):
                self.write('maximum_speed', label.encode())
                self.assertEqual(read_speed_attribute(self.root, 'maximum_speed')['speed'], expected)

    def test_unknown_is_not_reported_as_full_speed(self):
        for value in (b'', b'UNKNOWN', b'unknown', b'480', b'high-speed extra'):
            with self.subTest(value=value):
                self.write('current_speed', value)
                value = read_speed_attribute(self.root, 'current_speed')
                self.assertFalse(value['known'])
                self.assertIsNone(value['speed'])

    def test_missing_attributes_remain_unknown(self):
        report = udc_speed_report(self.root)
        self.assertEqual(report['maximum']['error'], 'missing')
        self.assertEqual(report['current_at_inspection']['error'], 'missing')

    def test_oversized_and_non_ascii_values(self):
        self.write('maximum_speed', b'x' * 129)
        self.assertEqual(read_speed_attribute(self.root, 'maximum_speed')['error'], 'oversized')
        self.write('maximum_speed', bytes((255,)))
        self.assertEqual(read_speed_attribute(self.root, 'maximum_speed')['error'], 'invalid_encoding')

    def test_permission_error_is_not_a_supported_mode(self):
        with patch.object(Path, 'open', side_effect=PermissionError):
            report = udc_speed_report(self.root)
        self.assertEqual(report['maximum']['error'], 'unreadable')
        self.assertIsNone(report['controller_supported_modes'])

    def test_attribute_path_is_allowlisted(self):
        for attribute in ('../uevent', '/dev/raw-gadget', 'speed', ''):
            with self.assertRaises(ValueError):
                read_speed_attribute(self.root, attribute)

    def test_superspeed_controller_does_not_enable_superspeed_backend(self):
        self.write('maximum_speed', b'super-speed-plus')
        self.assertEqual(SUPPORTED_MODEL_SPEEDS, (1, 2, 3))
        self.assertNotIn(5, [row['speed'] for row in udc_speed_report(self.root)['software_supported_speeds']])


if __name__ == '__main__':
    unittest.main()
