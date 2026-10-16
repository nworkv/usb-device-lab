"""Experimental SS EP0 tests with fake sysfs and fake Raw Gadget I/O."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from usb_device_lab import superspeed_control as ss
from usb_device_lab.model import DeviceConfig

ROOT = Path(__file__).resolve().parents[1]


class SuperSpeedControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.udc = Path(temporary.name)
        self.data = json.loads((ROOT / 'examples/superspeed-control-only.json').read_text(encoding='utf-8'))

    def attribute(self, name, value):
        (self.udc / name).write_text(value, encoding='ascii')

    def executor(self, io=None):
        device = ss.SuperSpeedControlDevice.__new__(ss.SuperSpeedControlDevice)
        device.data, device.udc, device.io = self.data, self.udc, io
        device.protocol, device.disable, device.control = Mock(), Mock(), Mock()
        return device

    def fake_io(self):
        io = Mock()
        self.initialized_speed = None
        fetched = 0
        def call(request, argument=None):
            nonlocal fetched
            if request == ss.INIT:
                self.initialized_speed = argument.raw[256]
            elif request == ss.FETCH:
                fetched += 1
                if fetched > 1:
                    raise RuntimeError('fixture end')
                argument[:16] = struct.pack('<IIBBHHH', 2, 8, 128, 6, 256, 0, 18)
            return 0
        io.call.side_effect = call
        return io

    def test_example_validates_only_through_explicit_model(self):
        before = copy.deepcopy(self.data)
        self.assertEqual(ss.ControlConfig(self.data).data, before)
        with self.assertRaises(ValueError):
            DeviceConfig(self.data)
        self.assertEqual(self.data, before)

    def test_requires_opt_in_and_bos(self):
        for flag in (False, 1, None):
            data = copy.deepcopy(self.data)
            data['runtime']['experimental_superspeed_control_only'] = flag
            with self.assertRaises(ValueError):
                ss.ControlConfig(data)
        data = copy.deepcopy(self.data)
        data['descriptors'] = [d for d in data['descriptors'] if d['type'] != 15]
        with self.assertRaises(ValueError):
            ss.ControlConfig(data)

    def test_runtime_data_endpoints_are_forbidden(self):
        data = copy.deepcopy(self.data)
        data['runtime']['configurations'][0]['interfaces'][0]['alternates'][0]['endpoints'] = [{'descriptor_hex': '07058102000200'}]
        with self.assertRaisesRegex(ValueError, 'forbids all runtime data endpoints'):
            ss.ControlConfig(data)

    def test_superplus_and_other_requested_speeds_are_rejected(self):
        for speed in (1, 2, 3, 6, True, '5'):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                ss.ControlConfig(dict(self.data, speed=speed))

    def test_insufficient_or_missing_ceiling_prevents_raw_io(self):
        for maximum in (None, 'full-speed', 'high-speed'):
            with self.subTest(maximum=maximum):
                path = self.udc / 'maximum_speed'
                if maximum is None:
                    path.unlink(missing_ok=True)
                else:
                    self.attribute('maximum_speed', maximum)
                device = self.executor()
                with patch.object(ss, 'RawIO') as raw, self.assertRaises(ValueError):
                    device.run()
                raw.assert_not_called()
                device.protocol.close.assert_called_once_with()

    def test_init_uses_five_and_control_requires_observed_superspeed(self):
        self.attribute('maximum_speed', 'super-speed-plus')
        self.attribute('current_speed', 'super-speed')
        io = self.fake_io()
        device = self.executor(io)
        with patch.object(ss.legacy, 'log'), self.assertRaisesRegex(RuntimeError, 'fixture end'):
            device.run()
        self.assertEqual(self.initialized_speed, 5)
        device.control.assert_called_once()
        io.close.assert_called_once_with()
        device.protocol.close.assert_called_once_with()

    def test_downgraded_unknown_or_superplus_link_has_no_control_response(self):
        self.attribute('maximum_speed', 'super-speed-plus')
        for current in ('high-speed', 'UNKNOWN', 'super-speed-plus'):
            with self.subTest(current=current):
                self.attribute('current_speed', current)
                io = self.fake_io()
                device = self.executor(io)
                with patch.object(ss.legacy, 'log'), self.assertRaisesRegex(RuntimeError, 'no fallback or control response'):
                    device.run()
                device.control.assert_not_called()
                io.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
