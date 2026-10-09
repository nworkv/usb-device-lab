import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.corpus_browser import CorpusBrowser, MAX_CONFIG
from usb_device_lab.model import DeviceConfig


def config_data(family='hid', profile='keyboard'):
    return {'schema_version': 1, 'udc_driver': 'dwc2', 'udc_device': 'test', 'speed': 2,
            'descriptors': [{'type': 1, 'hex': '120100020000004000000000000001020301'},
                            {'type': 2, 'hex': '090209000001008032'}],
            'metadata': {'family': family, 'profile': profile}}


class CorpusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seeds = self.root / 'seeds'
        self.seeds.mkdir()
        self.results = self.root / 'results'
        self.results.mkdir()
        self.config = SimpleNamespace(seed_dir=self.seeds, results_dir=self.results)
        self.browser = CorpusBrowser(self.config)
        patcher = patch('usb_device_lab.corpus_browser.select_seeds', side_effect=lambda config: sorted(self.seeds.glob('*.json')))
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self, name='hid-keyboard.json', data=None):
        path = self.seeds / name
        path.write_text(json.dumps(data or config_data()))
        return hashlib.sha256(name.encode()).hexdigest()

    def learned(self, data=None):
        device = DeviceConfig(data or config_data())
        directory = self.results / 'corpus'
        directory.mkdir(exist_ok=True)
        (directory / (device.digest + '.json')).write_text(device.canonical())
        with sqlite3.connect(self.results / 'runs.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS corpus(digest TEXT PRIMARY KEY, added REAL)')
            db.execute('INSERT OR IGNORE INTO corpus VALUES (?,0)', (device.digest,))
        return device.digest

    def test_selected_seeds_and_detail(self):
        ident = self.seed()
        result = self.browser.list()
        self.assertEqual(len(result['items']), 1)
        card = result['items'][0]
        self.assertEqual(card['id'], ident)
        self.assertEqual(card['family'], 'hid')
        self.assertEqual(card['descriptor_count'], 2)
        self.assertNotIn('config', card)
        self.assertEqual(self.browser.detail('seeds', ident)['config']['speed'], 2)

    def test_learned_index_is_read_only(self):
        ident = self.learned()
        path = self.results / 'runs.sqlite3'
        before = path.read_bytes()
        self.assertEqual(self.browser.detail('learned', ident)['digest'], ident)
        self.assertEqual(len(self.browser.list('learned')['items']), 1)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_learned_database_not_created(self):
        self.assertEqual(self.browser.list('learned')['items'], [])
        self.assertFalse((self.results / 'runs.sqlite3').exists())

    def test_unindexed_file_cannot_be_opened(self):
        ident = self.learned()
        other = 'b' * 64
        (self.results / 'corpus' / (other + '.json')).write_text(json.dumps(config_data()))
        with self.assertRaises(FileNotFoundError):
            self.browser.detail('learned', other)

    def test_digest_mismatch_is_reported(self):
        ident = self.learned()
        (self.results / 'corpus' / (ident + '.json')).write_text(json.dumps(config_data('audio', 'speaker')))
        self.assertFalse(self.browser.list('learned')['items'][0]['valid'])
        with self.assertRaises(ValueError):
            self.browser.detail('learned', ident)

    def test_filters_and_pagination(self):
        for index in range(55):
            self.seed(f'seed-{index:03}.json', config_data('hid', str(index)))
        first = self.browser.list()
        self.assertEqual(len(first['items']), 50)
        self.assertTrue(first['more'])
        second = self.browser.list(offset=first['next_offset'])
        self.assertEqual(len(second['items']), 5)
        self.assertFalse(second['more'])
        filtered = self.browser.list(family='hid', profile='3')
        self.assertEqual(len(filtered['items']), 1)

    def test_filter_scan_limit_keeps_cursor_progress(self):
        for index in range(205):
            self.seed(f'seed-{index:03}.json', config_data('hid', 'keyboard'))
        result = self.browser.list(family='audio')
        self.assertEqual(result['items'], [])
        self.assertEqual(result['scanned'], 200)
        self.assertEqual(result['next_offset'], 200)
        self.assertTrue(result['more'])

    def test_symlink_and_fifo_not_read(self):
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(config_data()))
        (self.seeds / 'linked.json').symlink_to(outside)
        ident = hashlib.sha256(b'linked.json').hexdigest()
        with self.assertRaises(OSError):
            self.browser.detail('seeds', ident)
        (self.seeds / 'linked.json').unlink()
        os.mkfifo(self.seeds / 'fifo.json')
        ident = hashlib.sha256(b'fifo.json').hexdigest()
        with self.assertRaises(ValueError):
            self.browser.detail('seeds', ident)

    def test_invalid_identifiers_parameters_and_oversize(self):
        for source, ident in (('seeds', '../outside'), ('unknown', 'a'*64)):
            with self.assertRaises(ValueError):
                self.browser.detail(source, ident)
        with self.assertRaises(ValueError):
            self.browser.list(offset=-1)
        ident = self.seed()
        (self.seeds / 'hid-keyboard.json').write_bytes(b'x' * (MAX_CONFIG + 1))
        with self.assertRaises(ValueError):
            self.browser.detail('seeds', ident)

    def test_nonfinite_metadata_is_rejected(self):
        data = config_data()
        data['metadata']['description'] = 'overflow'
        ident = self.seed(data=data)
        path = self.seeds / 'hid-keyboard.json'
        path.write_text(path.read_text().replace('"overflow"', '1e999'))
        with self.assertRaises(ValueError):
            self.browser.detail('seeds', ident)
        self.assertFalse(self.browser.list()['items'][0]['valid'])

    def test_missing_metadata_not_inferred_from_filename(self):
        data = config_data()
        data.pop('metadata')
        self.seed(data=data)
        card = self.browser.list()['items'][0]
        self.assertEqual(card['family'], 'unknown')
        self.assertEqual(card['profile'], 'unknown')


if __name__ == '__main__':
    unittest.main()
