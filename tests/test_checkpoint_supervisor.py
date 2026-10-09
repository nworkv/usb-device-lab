import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.control import CampaignSupervisor


@dataclass(frozen=True)
class Config:
    results_dir: Path
    iterations: int = 8
    seconds: float = 1
    seed: int = 42
    families: tuple = ()
    profiles: tuple = ()


class CheckpointSupervisorTests(unittest.TestCase):
    def test_resume_flag_and_cumulative_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def backend(config, stop, publish, resume=False):
                calls.append(resume)
                count = 1 if resume else 0
                publish({'checkpoint_progress': True, 'next_iteration': count, 'run': 'a'*32 if resume else None})
                publish({'iteration': count, 'run': 'b'*32})
                if not resume:
                    stop.set()
                return {'runs': 1, 'fatal': False}
            with patch('usb_device_lab.control.select_seeds', return_value=[]), patch('usb_device_lab.checkpoint_campaign.run_checkpoint_session', side_effect=backend):
                supervisor = CampaignSupervisor(Config(Path(directory)))
                supervisor.start()
                self.assertEqual(supervisor.wait(2)['state'], 'stopped')
                self.assertEqual(supervisor.status()['runs'], 1)
                supervisor.resume()
                self.assertEqual(supervisor.wait(2)['state'], 'completed')
                self.assertEqual(supervisor.status()['runs'], 2)
                self.assertEqual(calls, [False, True])

    def test_custom_three_argument_backend_still_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def backend(config, stop, publish):
                calls.append(True)
                return {'runs': 0, 'fatal': False}
            with patch('usb_device_lab.control.select_seeds', return_value=[]):
                supervisor = CampaignSupervisor(Config(Path(directory)), backend)
                supervisor.start()
                self.assertEqual(supervisor.wait(2)['state'], 'completed')
                supervisor.resume()
                self.assertEqual(supervisor.wait(2)['state'], 'completed')
                self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
