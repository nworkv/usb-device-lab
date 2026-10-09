import copy
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.checkpoint_campaign import run_checkpoint_session
from usb_device_lab.model import DeviceConfig


@dataclass(frozen=True)
class Config:
    results_dir: Path
    seed_dir: Path
    iterations: int = 8
    seconds: float = 1
    seed: int = 42
    families: tuple = ()
    profiles: tuple = ()


class TestStore:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'runs.sqlite3')
        self.db.executescript('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,started REAL,finished REAL,digest TEXT,status TEXT,result TEXT);'
                              'CREATE TABLE IF NOT EXISTS corpus(digest TEXT PRIMARY KEY,added REAL);')
        self.db.commit()
    def seed(self, device):
        directory = self.root / 'corpus'
        directory.mkdir(exist_ok=True)
        (directory / (device.digest + '.json')).write_text(device.canonical())
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO corpus VALUES (?,0)', (device.digest,))
    def corpus(self):
        return [self.root / 'corpus' / (row[0] + '.json') for row in self.db.execute('SELECT digest FROM corpus ORDER BY digest')]
    def begin(self, device, metadata):
        ident = uuid.uuid4().hex
        directory = self.root / 'runs' / ident
        directory.mkdir(parents=True)
        (directory / 'config.json').write_text(device.canonical())
        (directory / 'metadata.json').write_text(json.dumps(metadata))
        with self.db:
            self.db.execute('INSERT INTO runs VALUES (?,0,NULL,?,?,NULL)', (ident, device.digest, 'running'))
        return ident
    def finish(self, ident, device, result):
        self.seed(device)
        with self.db:
            self.db.execute('UPDATE runs SET status=?,finished=1,result=? WHERE id=?',
                            ('error' if result['errors'] else 'ok', json.dumps(result), ident))
        return 1
    def recover(self):
        with self.db:
            self.db.execute("UPDATE runs SET status='interrupted',finished=1 WHERE status='running'")
    def close(self):
        self.db.close()


class CheckpointCampaignTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
    def config(self, name):
        root = self.root / name
        seeds = root / 'seeds'
        seeds.mkdir(parents=True)
        data = {'schema_version': 1, 'udc_driver': 'dwc2', 'udc_device': 'test', 'speed': 2,
                'descriptors': [{'type': 1, 'hex': '120100020000004000000000000001020301'},
                                {'type': 2, 'hex': '090209000001008032'}]}
        (seeds / 'seed.json').write_text(json.dumps(data))
        return Config(root / 'results', seeds)
    def run_campaign(self, config, records, resume=False, stop_after=None, **overrides):
        stop = threading.Event()
        def publish(summary):
            if not summary.get('checkpoint_progress') and stop_after is not None and summary['iteration'] + 1 >= stop_after:
                stop.set()
        def execute(directory, agent, gadget, seconds):
            data = json.loads((Path(directory) / 'config.json').read_text())
            metadata = json.loads((Path(directory) / 'metadata.json').read_text())
            records.append((data, metadata['parent'], metadata['mutation']))
            return {'errors': [], 'coverage_valid': True, 'pcs': [], 'verdict': {'outcome': 'no_change'}}
        class Orchestrator:
            def __init__(self, config): pass
            def __enter__(self): return self
            def __exit__(self, *errors): return False
        values = dict(store_cls=TestStore, execute_fn=execute, checks_fn=lambda config: {'ready': True},
                      prepare_fn=lambda config: [config.seed_dir / 'seed.json'], orchestrator_cls=Orchestrator,
                      agent_fn=lambda config: [], identity_fn=lambda config: 'a'*64, engine_fn=lambda: 'b'*64)
        values.update(overrides)
        return run_checkpoint_session(config, stop, publish, resume=resume, **values)

    def test_stop_resume_matches_uninterrupted_inputs(self):
        full, split = self.config('full'), self.config('split')
        expected, actual = [], []
        self.run_campaign(full, expected)
        self.assertEqual(self.run_campaign(split, actual, stop_after=3)['runs'], 3)
        self.assertEqual(self.run_campaign(split, actual, resume=True)['runs'], 5)
        self.assertEqual(actual, expected)
        value = json.loads((split.results_dir / 'campaign-checkpoint.json').read_text())['checkpoint']
        self.assertEqual(value['next_iteration'], 8)

    def test_completed_budget_does_not_execute(self):
        config = self.config('completed')
        records = []
        self.run_campaign(config, records)
        with self.assertRaisesRegex(RuntimeError, 'completed'):
            self.run_campaign(config, records, resume=True)
        self.assertEqual(len(records), 8)

    def test_engine_change_does_not_execute(self):
        config = self.config('engine')
        records = []
        self.run_campaign(config, records, stop_after=2)
        with self.assertRaises(ValueError):
            self.run_campaign(config, records, resume=True, engine_fn=lambda: 'c'*64)
        self.assertEqual(len(records), 2)

    def test_changed_corpus_content_does_not_execute(self):
        config = self.config('corpus')
        records = []
        self.run_campaign(config, records, stop_after=2)
        path = next((config.results_dir / 'corpus').glob('*.json'))
        data = json.loads(path.read_text())
        data['udc_device'] = 'changed'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.run_campaign(config, records, resume=True)
        self.assertEqual(len(records), 2)

    def test_unfinished_attempt_is_not_silently_retried(self):
        config = self.config('unfinished')
        records = []
        def failed_execute(*args):
            raise OSError('executor launch failed')
        with self.assertRaises(OSError):
            self.run_campaign(config, records, execute_fn=failed_execute)
        with self.assertRaisesRegex(RuntimeError, 'Unfinished'):
            self.run_campaign(config, records, resume=True)
        self.assertEqual(records, [])

    def test_db_checkpoint_gap_without_new_corpus_is_rejected(self):
        config = self.config('gap')
        records = []
        from usb_device_lab.checkpoint_campaign import save_checkpoint
        calls = []
        def failed_save(path, value):
            calls.append(value['next_iteration'])
            if value['next_iteration'] > 0:
                raise OSError('checkpoint failed')
            return save_checkpoint(path, value)
        with patch('usb_device_lab.checkpoint_campaign.save_checkpoint', side_effect=failed_save):
            with self.assertRaises(OSError):
                self.run_campaign(config, records)
        self.assertEqual(calls, [0, 1])
        with self.assertRaisesRegex(RuntimeError, 'gap'):
            self.run_campaign(config, records, resume=True)
        self.assertEqual(len(records), 1)

    def test_missing_checkpoint_does_not_create_database(self):
        config = self.config('missing')
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            self.run_campaign(config, [], resume=True)
        self.assertFalse((config.results_dir / 'runs.sqlite3').exists())

    def test_campaign_lock_prevents_concurrent_execution(self):
        import fcntl
        config = self.config('locked')
        config.results_dir.mkdir(parents=True)
        records = []
        with open(config.results_dir / 'campaign.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_campaign(config, records)
        self.assertEqual(records, [])

    def test_fatal_attempt_checkpoint_cannot_resume(self):
        config = self.config('fatal')
        records = []
        def fatal_execute(*args):
            return {'errors': [{'kind': 'cleanup', 'summary': 'unreaped'}], 'coverage_valid': False, 'pcs': []}
        self.assertTrue(self.run_campaign(config, records, execute_fn=fatal_execute)['fatal'])
        with self.assertRaisesRegex(RuntimeError, 'fatally'):
            self.run_campaign(config, records, resume=True)
        self.assertEqual(records, [])

    def test_failed_preflight_does_not_execute(self):
        config = self.config('preflight')
        records = []
        with self.assertRaisesRegex(RuntimeError, 'checks failed'):
            self.run_campaign(config, records, checks_fn=lambda config: {'ready': False})
        self.assertEqual(records, [])


if __name__ == '__main__':
    unittest.main()
