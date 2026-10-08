import json
import os
import tempfile
import threading
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.persistent_control import PersistentCampaignSupervisor


@dataclass(frozen=True)
class Config:
    results_dir: Path
    path: Path
    host: str = '127.0.0.1'
    usb_bus: int = 1
    udc: str = 'test-udc'
    iterations: int = 2
    seconds: float = 1
    seed: int = 0
    families: tuple = ()
    profiles: tuple = ()


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = Config(self.root, self.root / 'lab.toml')
        patcher = patch('usb_device_lab.control.select_seeds', return_value=['seed'])
        patcher.start()
        self.addCleanup(patcher.stop)

    def completed(self, config, stop, publish):
        publish({'run': 'one'})
        return {'runs': 1, 'fatal': False}

    def test_state_survives_reopen_and_resume_has_new_session(self):
        with PersistentCampaignSupervisor(self.config, self.completed) as first:
            first.start(seed=42)
            status = first.wait(2)
            self.assertEqual(status['state'], 'completed')
            session = status['session']
        with PersistentCampaignSupervisor(self.config, self.completed) as second:
            self.assertEqual(second.status()['runs'], 1)
            self.assertEqual(second._last['seed'], 42)
            self.assertNotEqual(second.resume()['session'], session)
            self.assertEqual(second.wait(2)['state'], 'completed')

    def test_active_snapshot_becomes_interrupted_without_autostart(self):
        with PersistentCampaignSupervisor(self.config) as supervisor:
            supervisor._status['state'] = 'running'
            supervisor._save()
            data = json.loads((self.root / 'supervisor-state.json').read_text())
        data['status']['state'] = 'running'
        (self.root / 'supervisor-state.json').write_text(json.dumps(data))
        with PersistentCampaignSupervisor(self.config, lambda *args: self.fail('autostart')) as restored:
            self.assertEqual(restored.status()['state'], 'interrupted')
            self.assertIsNone(restored._thread)

    def test_second_owner_rejected_and_lock_released(self):
        first = PersistentCampaignSupervisor(self.config)
        try:
            with self.assertRaises(BlockingIOError):
                PersistentCampaignSupervisor(self.config)
        finally:
            first.close()
        with PersistentCampaignSupervisor(self.config):
            pass

    def test_corrupt_snapshot_releases_owner_lock(self):
        path = self.root / 'supervisor-state.json'
        path.write_text('{')
        with self.assertRaises(ValueError):
            PersistentCampaignSupervisor(self.config)
        path.unlink()
        with PersistentCampaignSupervisor(self.config):
            pass

    def test_wrong_lab_identity_rejected(self):
        with PersistentCampaignSupervisor(self.config):
            pass
        with self.assertRaises(ValueError):
            PersistentCampaignSupervisor(replace(self.config, usb_bus=2))

    def test_write_failure_preserves_old_snapshot_and_stops(self):
        with PersistentCampaignSupervisor(self.config) as supervisor:
            supervisor._save()
            path = self.root / 'supervisor-state.json'
            previous = path.read_bytes()
            with patch('usb_device_lab.persistent_control.os.replace', side_effect=OSError('disk failure')):
                supervisor._save()
            self.assertEqual(path.read_bytes(), previous)
            self.assertTrue(supervisor._stop.is_set())
            self.assertIn('disk failure', supervisor.status()['persistence_error'])
            self.assertEqual(list(self.root.glob('.supervisor-*')), [])
            with self.assertRaises(RuntimeError):
                supervisor.start()

    def test_close_stops_worker_and_rejects_start(self):
        entered = threading.Event()
        def backend(config, stop, publish):
            entered.set()
            if not stop.wait(2):
                raise RuntimeError('stop not requested')
            return {'runs': 0, 'fatal': False}
        supervisor = PersistentCampaignSupervisor(self.config, backend)
        supervisor.start()
        self.assertTrue(entered.wait(1))
        supervisor.close()
        supervisor.close()
        self.assertEqual(supervisor.status()['state'], 'stopped')
        with self.assertRaises(RuntimeError):
            supervisor.start()

    def test_snapshot_permissions(self):
        with PersistentCampaignSupervisor(self.config) as supervisor:
            supervisor._save()
            self.assertEqual(os.stat(self.root / 'supervisor-state.json').st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
