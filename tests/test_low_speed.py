"""Low-speed model validation; these tests do not establish UDC compatibility."""
import copy
from pathlib import Path
import tempfile
import unittest
from usb_device_lab.model import DeviceConfig


def device(speed=1, transfer=3, size=8, interval=10):
    endpoint=bytes((7,5,129,transfer,size&255,size>>8,interval)).hex()
    return dict(schema_version=1,udc_driver='dummy_udc',udc_device='dummy_udc.0',speed=speed,
        descriptors=[dict(type=1,hex='12011001000000086b1d1001000101020301'),dict(type=2,hex='0902190001010080320904000001030101000705810308000a')],
        runtime=dict(endpoints=[dict(descriptor_hex=endpoint,payload_hex='0000000000000000')]))


class LowSpeedTests(unittest.TestCase):
    def test_low_speed_interrupt_sizes_and_intervals(self):
        for size in (1,8):
            for interval in (10,255):
                with self.subTest(size=size,interval=interval):
                    self.assertEqual(DeviceConfig(device(size=size,interval=interval)).data['speed'],1)

    def test_low_speed_rejects_non_interrupt_runtime(self):
        for transfer in (0,1,2):
            with self.subTest(transfer=transfer),self.assertRaises(ValueError):
                DeviceConfig(device(transfer=transfer))

    def test_low_speed_rejects_oversize_and_fast_polling(self):
        for size in (0,9,64,512):
            with self.subTest(size=size),self.assertRaises(ValueError):
                DeviceConfig(device(size=size))
        for interval in (0,1,9):
            with self.subTest(interval=interval),self.assertRaises(ValueError):
                DeviceConfig(device(interval=interval))

    def test_full_and_high_speed_regression(self):
        for speed,transfer,size in ((2,2,64),(2,3,64),(3,2,512),(3,3,1024)):
            with self.subTest(speed=speed,transfer=transfer,size=size):
                DeviceConfig(device(speed=speed,transfer=transfer,size=size,interval=1))
        with self.assertRaises(ValueError):
            DeviceConfig(device(speed=3,transfer=2,size=64))

    def test_unsupported_speeds_remain_rejected(self):
        for speed in (0,4,5,6,True,'1'):
            with self.subTest(speed=speed),self.assertRaises(ValueError):
                DeviceConfig(device(speed=speed))

    def test_round_trip_and_input_immutability(self):
        raw=device()
        before=copy.deepcopy(raw)
        config=DeviceConfig(raw)
        self.assertEqual(raw,before)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'device.json'
            config.save(path)
            self.assertEqual(DeviceConfig.load(path).digest,config.digest)


if __name__=='__main__':
    unittest.main()
