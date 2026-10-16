"""Real DeviceConfig/Mutator corpus integration; no USB or network connections."""
from collections import Counter
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from usb_device_lab.corpus_variants import build, usb_digest
from usb_device_lab.model import DeviceConfig
from usb_device_lab.uart_pipeline import select_seeds

ROOT = Path(__file__).resolve().parents[1]


class CorpusVariantIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.output = cls.root / 'all-profiles'
        cls.report = build(ROOT / 'corpus/seeds', ROOT / 'corpus/manifest.toml', cls.output, count=50, seed=42)
        cls.inventory = json.loads((cls.output / 'inventory.json').read_text(encoding='utf-8'))

    def test_fifty_model_valid_distinct_variants_per_source(self):
        entries = self.inventory['entries']
        counts = Counter(entry['group'] for entry in entries)
        self.assertEqual(len(counts), self.report['source_profiles'])
        self.assertEqual(set(counts.values()), {50})
        self.assertEqual(len(entries), 50 * self.report['source_profiles'])
        identities = {}
        for entry in entries:
            with self.subTest(path=entry['path']):
                device = DeviceConfig.load(self.output / entry['path'])
                self.assertEqual(device.digest, entry['digest'])
                self.assertEqual(usb_digest(device), entry['usb_digest'])
                identities.setdefault(entry['group'], set()).add(entry['usb_digest'])
        self.assertTrue(all(len(values) == 50 for values in identities.values()))
        complete = json.loads((self.output / 'complete.json').read_text())
        self.assertEqual(complete['configurations'], len(entries))

    def test_generated_manifest_is_consumed_by_real_campaign_selector(self):
        for group in sorted({entry['group'] for entry in self.inventory['entries']}):
            with self.subTest(group=group):
                config = SimpleNamespace(manifest=self.output / 'manifest.toml', seed_dir=self.output / 'seeds',
                    families=(group,), profiles=())
                selected = select_seeds(config)
                self.assertEqual(len(selected), 50)
                self.assertEqual({row['family'] for row in selected}, {group})
                self.assertEqual(len({row['digest'] for row in selected}), 50)

    def test_profile_generation_does_not_depend_on_other_profiles(self):
        manifest = self.root / 'keyboard-only.toml'
        manifest.write_text('[corpus]' + chr(10) + 'format=1' + chr(10) + '[[family]]' + chr(10) + 'name="hid"' + chr(10) + 'profiles=["keyboard"]' + chr(10), encoding='utf-8')
        output = self.root / 'keyboard-only'
        build(ROOT / 'corpus/seeds', manifest, output, count=50, seed=42)
        for entry in self.inventory['entries']:
            if entry['group'] == 'hid-keyboard':
                self.assertEqual((self.output / entry['path']).read_bytes(), (output / entry['path']).read_bytes())

    def test_existing_output_is_not_overwritten(self):
        before = (self.output / 'inventory.json').read_bytes()
        with self.assertRaises(FileExistsError):
            build(ROOT / 'corpus/seeds', ROOT / 'corpus/manifest.toml', self.output, count=50, seed=42)
        self.assertEqual((self.output / 'inventory.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
