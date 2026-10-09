import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from usb_device_lab.uart_campaign import run_campaign, terminate

class TestReceiver:
    def __init__(self, settings, directory):
        self.state, self.closed = 'new', threading.Event()
    def run(self):
        self.state = 'connected'
        self.closed.wait()
        self.state = 'stopped'
    def status(self):
        return dict(state=self.state, completeness='unknown')
    def stop(self):
        self.closed.set()

class FailedReceiver(TestReceiver):
    def run(self):
        self.state = 'failed'
        raise OSError('UART fixture failure')

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = SimpleNamespace(telemetry_mode='uart', feedback='none', strategy='round-robin',
            iterations=2, seconds=0.06, seed=7, pre_run_quiet_seconds=0.01,
            post_run_capture_seconds=0.01, udc='test.udc', gadget_udc_driver='test-driver',
            results_dir=self.root / 'results', logs_dir=self.root / 'logs',
            uart=SimpleNamespace(read_timeout=0.02))
        self.seed = self.root / 'seed.json'
        self.seed.write_text(json.dumps(dict(schema_version=1, udc_driver='dummy_udc',
            udc_device='dummy_udc.0', descriptors=[dict(type=1, hex='12010002000000400df00300000100000001'),
            dict(type=2, hex='090209000001008032')], runtime=dict(endpoints=[]))))
        self.processes = []
        self.receivers = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for p in self.processes:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=2)

    def receiver(self, settings, directory):
        r = TestReceiver(settings, directory)
        self.receivers.append(r)
        return r

    def command(self, path):
        return [sys.executable, '-u', '-c', 'import time; print("ready",flush=True); time.sleep(60)']

    def invoke(self, **kwargs):
        values = dict(receiver_factory=self.receiver, command_factory=self.command,
                      lock_root=self.root / 'locks')
        values.update(kwargs)
        original = subprocess.Popen
        def spawn(*args, **opts):
            p = original(*args, **opts)
            self.processes.append(p)
            return p
        with patch('usb_device_lab.uart_campaign.subprocess.Popen', side_effect=spawn):
            return run_campaign(self.config, values.pop('seeds', [self.seed]), **values)

    def results(self):
        return list(self.config.results_dir.glob('*/attempt-*/result.json'))

    def test_continuous_receiver_boundaries_and_mutations(self):
        result = self.invoke()
        self.assertEqual(len(result['outcomes']), 2)
        self.assertEqual(len(self.receivers), 1)
        self.assertEqual(self.receivers[0].state, 'stopped')
        self.assertTrue(all(p.poll() is not None for p in self.processes))
        root = Path(result['directory'])
        for path in sorted(root.glob('attempt-*')):
            window = json.loads((path / 'window.json').read_text())
            self.assertLessEqual(window['start_ns'], window['active_start_ns'])
            self.assertLessEqual(window['active_start_ns'], window['active_end_ns'])
            self.assertLess(window['active_end_ns'], window['end_ns'])
            self.assertEqual(window['capture_id'], result['campaign_id'])
            data = json.loads((path / 'config.json').read_text())
            self.assertEqual((data['udc_device'], data['udc_driver']), ('test.udc', 'test-driver'))
            outcome = json.loads((path / 'result.json').read_text())
            self.assertTrue(outcome['window_completed'])
            self.assertEqual(outcome['pcs'], [])
        self.assertTrue(json.loads((root / 'attempt-000001' / 'started.json').read_text())['mutation'])

    def test_failed_receiver_never_launches_executor(self):
        with self.assertRaisesRegex(RuntimeError, 'UART receiver failed'):
            self.invoke(receiver_factory=FailedReceiver)
        self.assertEqual(self.processes, [])

    def test_early_exit_halts_campaign(self):
        self.config.seconds = 1
        with self.assertRaisesRegex(RuntimeError, 'before window completion'):
            self.invoke(command_factory=lambda p: [sys.executable, '-c', 'raise SystemExit(2)'])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(self.results()), 1)
        self.assertFalse(json.loads(self.results()[0].read_text())['window_completed'])

    def test_cancel_before_start(self):
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.invoke(stop=stop)['outcomes'], [])
        self.assertEqual(self.processes, [])

    def test_cancel_active_attempt_reaps_and_persists(self):
        stop = threading.Event()
        def command(path):
            timer = threading.Timer(0.03, stop.set)
            timer.start()
            self.addCleanup(timer.join)
            return self.command(path)
        self.config.seconds = 2
        result = self.invoke(stop=stop, command_factory=command)
        self.assertEqual(len(result['outcomes']), 1)
        self.assertTrue(result['outcomes'][0]['interrupted'])
        self.assertTrue(all(p.poll() is not None for p in self.processes))

    def test_receiver_failure_during_active_window_reaps(self):
        def command(path):
            timer = threading.Timer(0.03, lambda: setattr(self.receivers[0], 'state', 'failed'))
            timer.start()
            self.addCleanup(timer.join)
            return self.command(path)
        self.config.seconds = 2
        with self.assertRaisesRegex(RuntimeError, 'UART receiver failed'):
            self.invoke(command_factory=command)
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].poll())

    def test_popen_failure_persists_window_and_stops(self):
        with self.assertRaisesRegex(RuntimeError, 'No such file'):
            self.invoke(command_factory=lambda p: ['/nonexistent/uart-test-executor'])
        self.assertEqual(len(self.results()), 1)
        self.assertTrue(json.loads(self.results()[0].read_text())['infrastructure_error'])
        self.assertEqual(self.receivers[0].state, 'stopped')

    def test_cleanup_failure_no_next_attempt(self):
        with patch('usb_device_lab.uart_campaign.terminate', side_effect=RuntimeError('not reaped')):
            with self.assertRaisesRegex(RuntimeError, 'not reaped'):
                self.invoke()
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(self.results()), 1)

    def test_storage_failure_no_launch(self):
        with patch('usb_device_lab.uart_campaign.save', side_effect=OSError('disk failed')):
            with self.assertRaisesRegex(OSError, 'disk failed'):
                self.invoke()
        self.assertEqual(self.processes, [])
        self.assertEqual(self.receivers, [])

    def test_udc_lock_excludes_second_campaign(self):
        root = self.root / 'locks'
        root.mkdir(mode=0o700)
        path = root / (hashlib.sha256(self.config.udc.encode()).hexdigest() + '.lock')
        with path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.invoke()
        self.assertEqual(self.receivers, [])

    def test_invalid_duration_or_missing_seed_no_launch(self):
        self.config.seconds = float('nan')
        with self.assertRaises(ValueError):
            self.invoke()
        self.config.seconds = 0.06
        self.seed.unlink()
        with self.assertRaises(FileNotFoundError):
            self.invoke()
        self.assertEqual(self.processes, [])

    def test_unreaped_marker_blocks_retry(self):
        with patch('usb_device_lab.uart_campaign.terminate', side_effect=RuntimeError('not reaped')):
            with self.assertRaisesRegex(RuntimeError, 'not reaped'):
                self.invoke()
        before = len(self.processes)
        with self.assertRaisesRegex(RuntimeError, 'cleanup marker'):
            self.invoke()
        self.assertEqual(len(self.processes), before)

    def test_explicit_seeds_round_robin(self):
        second = self.root / 'second.json'
        data = json.loads(self.seed.read_text())
        data['label'] = 'second'
        second.write_text(json.dumps(data))
        self.config.iterations = 3
        result = self.invoke(seeds=[self.seed, second])
        root = Path(result['directory'])
        labels = [json.loads(p.read_text()).get('label') for p in sorted(root.glob('attempt-*/config.json'))]
        self.assertEqual(labels, [None, 'second', None])

    def test_real_child_ignoring_term_is_killed(self):
        p = subprocess.Popen([sys.executable, '-u', '-c',
            'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(60)'],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.processes.append(p)
        try:
            self.assertEqual(p.stdout.readline().strip(), 'ready')
            terminate(p, term_timeout=0.03, kill_timeout=2)
            self.assertEqual(p.returncode, -signal.SIGKILL)
        finally:
            p.stdout.close()

if __name__ == '__main__':
    unittest.main()
