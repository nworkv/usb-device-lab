import json
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab import uart_checkpoint as cp
from usb_device_lab.uart_campaign import run_campaign

class Receiver:
    def __init__(self, settings, directory):
        self.state = 'new'
        self.done = threading.Event()
    def run(self):
        self.state = 'connected'
        self.done.wait()
        self.state = 'stopped'
    def status(self):
        return {'state': self.state}
    def stop(self):
        self.done.set()

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'checkpoint.json'
        self.rng = random.Random(7)
        self.digest, self.capture = 'f' * 64, 'a' * 32

    def snapshot(self):
        cp.write(self.path, self.digest, 1, self.rng, self.capture)

    def test_prng_roundtrip(self):
        self.snapshot()
        data = cp.load(self.path, self.digest, 3)
        restored = random.Random()
        restored.setstate(data['prng'])
        self.assertEqual([self.rng.getrandbits(64) for _ in range(30)], [restored.getrandbits(64) for _ in range(30)])

    def test_compatibility_and_legacy_rejected(self):
        self.snapshot()
        with self.assertRaises(ValueError):
            cp.load(self.path, 'e' * 64, 3)
        self.path.write_text('{"schema_version":1,"pcs":[]}')
        with self.assertRaises(ValueError):
            cp.load(self.path, self.digest, 3)

    def test_invalid_progress_and_words(self):
        for key, value in [('next_iteration', True), ('next_iteration', 4), ('source_capture', '../x'), ('prng', [3, [1], None])]:
            self.snapshot()
            data = json.loads(self.path.read_text())
            data[key] = value
            self.path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                cp.load(self.path, self.digest, 3)

    def test_atomic_write_failure_preserves_old_file(self):
        self.snapshot()
        before = self.path.read_bytes()
        with patch('usb_device_lab.uart_checkpoint.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                cp.write(self.path, self.digest, 2, self.rng, self.capture)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.checkpoint-*')), [])

    def test_size_symlink_and_permissions(self):
        self.snapshot()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        link = self.root / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            cp.load(link, self.digest, 3)
        self.path.write_bytes(b'x' * 65537)
        with self.assertRaises(ValueError):
            cp.load(self.path, self.digest, 3)

    def campaign_config(self):
        return SimpleNamespace(telemetry_mode='uart', feedback='none', strategy='round-robin',
            iterations=3, seconds=0.04, seed=7, pre_run_quiet_seconds=0,
            post_run_capture_seconds=0, udc='test.udc', gadget_udc_driver='test-driver',
            results_dir=self.root / 'results', logs_dir=self.root / 'logs',
            uart=SimpleNamespace(port='/dev/test', read_timeout=0.01))

    def seed(self):
        path = self.root / 'seed.json'
        path.write_text(json.dumps(dict(schema_version=1, udc_driver='dummy_udc', udc_device='dummy_udc.0',
            descriptors=[dict(type=1, hex='12010002000000400df00300000100000001'), dict(type=2, hex='090209000001008032')], runtime={'endpoints':[]})))
        return path

    def command(self, path):
        return [sys.executable, '-c', 'import time; time.sleep(60)']

    def test_resume_repeats_interrupted_mutation_in_new_capture(self):
        cfg, seed, stop = self.campaign_config(), self.seed(), threading.Event()
        timers = []
        def command(path):
            if path.parent.name == 'attempt-000002':
                timer = threading.Timer(0.005, stop.set)
                timers.append(timer)
                timer.start()
            return self.command(path)
        first = run_campaign(cfg, [seed], stop=stop, receiver_factory=Receiver,
            command_factory=command, lock_root=self.root / 'locks')
        for timer in timers:
            timer.join()
        old = Path(first['directory'])
        self.assertEqual(json.loads((old / 'checkpoint.json').read_text())['next_iteration'], 2)
        repeated = run_campaign(cfg, [seed], receiver_factory=Receiver, command_factory=self.command,
            lock_root=self.root / 'locks', resume=old / 'checkpoint.json')
        new = Path(repeated['directory'])
        self.assertNotEqual(first['campaign_id'], repeated['campaign_id'])
        self.assertEqual((old / 'attempt-000002/config.json').read_bytes(), (new / 'attempt-000002/config.json').read_bytes())
        self.assertEqual([x['iteration'] for x in repeated['outcomes']], [2])
        self.assertEqual(json.loads((new / 'campaign.json').read_text())['resumed_from'], first['campaign_id'])
        self.assertEqual(json.loads((new / 'checkpoint.json').read_text())['next_iteration'], 3)
        with self.assertRaisesRegex(ValueError, 'already complete'):
            run_campaign(cfg, [seed], resume=new / 'checkpoint.json')

    def test_changed_settings_rejected_before_receiver(self):
        cfg, seed, stop = self.campaign_config(), self.seed(), threading.Event()
        stop.set()
        first = run_campaign(cfg, [seed], stop=stop, receiver_factory=Receiver,
            command_factory=self.command, lock_root=self.root / 'locks')
        path = Path(first['directory']) / 'checkpoint.json'
        cfg.uart.port = '/dev/changed'
        with patch('usb_device_lab.uart_campaign.UARTReceiver') as receiver:
            with self.assertRaises(ValueError):
                run_campaign(cfg, [seed], resume=path, receiver_factory=receiver)
            receiver.assert_not_called()

if __name__ == '__main__':
    unittest.main()
