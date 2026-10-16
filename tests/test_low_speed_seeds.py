"""Low Speed baseline seeds in the bundled corpus; model-level checks only."""
import json
import unittest
from pathlib import Path

from usb_device_lab.model import DeviceConfig

ROOT=Path(__file__).resolve().parents[1]
SEEDS=ROOT/'corpus'/'seeds'
FAMILY='hid-low-speed'
PROFILES=('keyboard','mouse','consumer-control','gamepad','multi-report')


def load(profile):
    return DeviceConfig.load(SEEDS/f'{FAMILY}-{profile}.json')


def seed_file(profile):
    return (SEEDS/f'{FAMILY}-{profile}.json').read_text(encoding='utf-8')


class LowSpeedSeedTests(unittest.TestCase):
    def test_manifest_lists_low_speed_family(self):
        import tomllib
        data=tomllib.loads((ROOT/'corpus'/'manifest.toml').read_text(encoding='utf-8'))
        row=next(f for f in data['family'] if f['name']==FAMILY)
        self.assertEqual(row['profiles'],list(PROFILES))

    def test_all_low_speed_seeds_validate(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                device=load(profile)
                self.assertEqual(device.data['speed'],1)
                self.assertEqual(device.data['metadata']['family'],FAMILY)
                self.assertEqual(device.data['metadata']['profile'],profile)

    def test_low_speed_seed_ep0_and_endpoint_bounds(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                data=load(profile).data
                entry=next(d for d in data['descriptors'] if d['type']==1)
                raw=bytes.fromhex(entry['hex'])
                self.assertEqual(raw[7],8,'bMaxPacketSize0 must be 8 for Low Speed')
                for configuration in data['runtime']['configurations']:
                    for interface in configuration['interfaces']:
                        for alternate in interface['alternates']:
                            for endpoint in alternate.get('endpoints',[]):
                                ep=bytes.fromhex(endpoint['descriptor_hex'])
                                self.assertEqual(ep[3]&3,3,'Low Speed allows interrupt endpoints only')
                                self.assertLessEqual(int.from_bytes(ep[4:6],'little'),8)
                                self.assertGreaterEqual(ep[6],10)

    def test_low_speed_seeds_are_stable_and_canonical(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                device=load(profile)
                self.assertEqual(seed_file(profile),device.canonical()+'\n')

    def test_low_speed_seed_report_descriptors_are_real(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                data=load(profile).data
                reports=[d for d in data['descriptors'] if d['type']==0x22]
                self.assertTrue(reports)
                for report in reports:
                    self.assertTrue(report['hex'])
                payload=bytes.fromhex(data['runtime']['configurations'][0]['interfaces'][0]['alternates'][0]['endpoints'][0]['payload_hex'])
                self.assertTrue(payload)


if __name__=='__main__': unittest.main()
