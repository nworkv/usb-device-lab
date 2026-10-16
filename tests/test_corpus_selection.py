"""Manifest browsing does not load seeds or mutate on-disk configuration."""
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from usb_device_lab.corpus_selection import catalogue, apply_family, SCRIPT


@dataclass(frozen=True)
class Config:
    manifest: Path
    families: tuple = ('original',)
    profiles: tuple = ('original/profile',)


class CorpusSelectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'manifest.toml'
        self.path.write_text('[corpus]' + chr(10) + 'format=1' + chr(10) + '[[family]]' + chr(10) + 'name="hid-keyboard"' + chr(10) + 'profiles=[' + ','.join('"v%04d"' % i for i in range(50)) + ']' + chr(10), encoding='utf-8')
        self.config = Config(self.path)

    def test_catalogue_counts_fifty_variants_without_seed_files(self):
        value = catalogue(self.config)
        self.assertEqual(value['profiles'], 50)
        self.assertEqual(value['families'], [{'name': 'hid-keyboard', 'count': 50}])

    def test_pick_and_restore_do_not_modify_base(self):
        selected = apply_family(self.config, 'hid-keyboard')
        self.assertEqual(selected.families, ('hid-keyboard',))
        self.assertEqual(selected.profiles, ())
        self.assertEqual(self.config.families, ('original',))
        self.assertIs(apply_family(self.config, None), self.config)
        self.assertEqual(apply_family(self.config, '').families, ())

    def test_unknown_and_invalid_family(self):
        for family in ('unknown', '../escape', True, 12, [], 'x' * 65):
            with self.assertRaises(ValueError):
                apply_family(self.config, family)

    def test_large_all_selection_rejected_but_group_allowed(self):
        self.path.write_text('[corpus]' + chr(10) + 'format=1' + chr(10) + ''.join('[[family]]' + chr(10) + 'name="group%d"' % n + chr(10) + 'profiles=[' + ','.join('"v%d"' % i for i in range(50)) + ']' + chr(10) for n in range(3)))
        with self.assertRaises(ValueError):
            apply_family(self.config, '')
        self.assertEqual(apply_family(self.config, 'group1').families, ('group1',))

    def test_invalid_manifest_rejected(self):
        for payload in ('[corpus]' + chr(10) + 'format=true', '[corpus]' + chr(10) + 'format=1' + chr(10) + '[[family]]' + chr(10) + 'name="hid"' + chr(10) + 'profiles=["x","x"]', 'x' * 65537):
            self.path.write_text(payload)
            with self.assertRaises(ValueError):
                catalogue(self.config)

    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_script_syntax(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == '__main__':
    unittest.main()
