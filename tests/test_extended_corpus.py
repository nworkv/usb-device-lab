"""Extended baselines validated with the real model and script protocol."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from usb_device_lab.extended_corpus import build, low_speed
from usb_device_lab.model import DeviceConfig
from usb_device_lab.protocol import load_protocol
from usb_device_lab.topology import validate_topology

ROOT = Path(__file__).resolve().parents[1]


class ExtendedCorpusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seeds = ROOT / 'corpus/seeds'
        self.manifest = self.root / 'source.toml'
        self.manifest.write_text('[corpus]' + chr(10) + 'format=1' + chr(10) + '[[family]]' + chr(10) + 'name="hid"' + chr(10) + 'profiles=["keyboard","consumer-control"]' + chr(10))

    def generate(self, name='extended'):
        output = self.root / name
        report = build(self.seeds, self.manifest, output)
        return output, report

    def test_added_profiles_validate_and_complete_marker_matches(self):
        output, report = self.generate()
        self.assertEqual(report['profile_count'], 5)
        self.assertEqual(report['additional_profiles'], 3)
        inventory = json.loads((output/'inventory.json').read_text())
        for entry in inventory['entries']:
            device = DeviceConfig.load(output/entry['path'])
            self.assertEqual(device.digest, entry['digest'])
        self.assertEqual(json.loads((output/'complete.json').read_text())['profile_count'], 5)
        self.assertFalse(report['hardware_checked'])

    def test_low_speed_ep0_runtime_and_language_selectors(self):
        output, _ = self.generate()
        for profile in ('keyboard-low-speed','consumer-control-low-speed'):
            device = DeviceConfig.load(output/'seeds'/('hid-'+profile+'.json'))
            self.assertEqual(device.data['speed'], 1)
            descriptor = next(d for d in device.data['descriptors'] if d['type']==1)
            raw = bytes.fromhex(descriptor['hex'])
            self.assertEqual(raw[7], 8)
            self.assertEqual(int.from_bytes(raw[2:4],'little'), 0x0110)
            self.assertTrue(all(d.get('wIndex')==1033 for d in device.data['descriptors'] if d['type']==3 and d.get('index',0)>0))
            for config in validate_topology(device.data).values():
                for interface in config['interfaces']:
                    for alternate in interface['alternates']:
                        for endpoint in alternate.get('endpoints',[]):
                            raw = bytes.fromhex(endpoint['descriptor_hex'])
                            self.assertEqual(raw[3]&3, 3)
                            self.assertLessEqual(int.from_bytes(raw[4:6],'little'), 8)

    def test_vendor_script_echoes_host_payload(self):
        output, _ = self.generate()
        device = DeviceConfig.load(output/'seeds/vendor-bulk-loopback.json')
        protocol = load_protocol(device.data['protocol'])
        try:
            payload = bytes(range(64))
            protocol.out(1, payload)
            self.assertEqual(protocol.incoming(129), payload)
            self.assertIsNone(protocol.incoming(129))
        finally:
            protocol.close()

    def test_reproducible_output_and_unmodified_sources(self):
        before = {name:(self.seeds/name).read_bytes() for name in ('hid-keyboard.json','hid-consumer-control.json')}
        first, _ = self.generate('first')
        second, _ = self.generate('second')
        self.assertEqual((first/'inventory.json').read_bytes(), (second/'inventory.json').read_bytes())
        for name, data in before.items():
            self.assertEqual((self.seeds/name).read_bytes(), data)

    def test_existing_output_is_preserved(self):
        output, _ = self.generate()
        before = (output/'inventory.json').read_bytes()
        with self.assertRaises(FileExistsError):
            build(self.seeds, self.manifest, output)
        self.assertEqual((output/'inventory.json').read_bytes(), before)

    def test_incompatible_source_runtime_is_not_silently_converted(self):
        data = copy.deepcopy(DeviceConfig.load(self.seeds/'hid-keyboard.json').data)
        data['runtime']['configurations'][0]['interfaces'][0]['alternates'][0]['endpoints'][0]['descriptor_hex']='07058102080000'
        with self.assertRaisesRegex(ValueError, 'not Low Speed compatible'):
            low_speed(DeviceConfig(data), 'bad-low-speed', 'Invalid source')


if __name__=='__main__':
    unittest.main()
