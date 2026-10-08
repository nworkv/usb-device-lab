import threading
import unittest
from dataclasses import dataclass
from unittest.mock import patch
from usb_device_lab.control import CampaignSupervisor


@dataclass(frozen=True)
class Config:
    iterations: int = 3
    seconds: float = 1
    seed: int = 0
    families: tuple = ()
    profiles: tuple = ()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('usb_device_lab.control.select_seeds', return_value=['seed'])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_completion_and_status_snapshot(self):
        def backend(config, stop, publish):
            publish({'run': 'one'})
            return {'runs': 1, 'fatal': False}
        supervisor = CampaignSupervisor(Config(), backend)
        supervisor.start()
        status = supervisor.wait(2)
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(status['runs'], 1)
        status['state'] = 'modified'
        self.assertEqual(supervisor.status()['state'], 'completed')

    def test_stop_and_resume_new_session(self):
        entered = threading.Event()
        def backend(config, stop, publish):
            entered.set()
            if not stop.wait(2):
                raise RuntimeError('test did not stop campaign')
            return {'runs': 0, 'fatal': False}
        supervisor = CampaignSupervisor(Config(), backend)
        first = supervisor.start()['session']
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):
            supervisor.start()
        self.assertEqual(supervisor.stop()['state'], 'stopping')
        self.assertEqual(supervisor.wait(2)['state'], 'stopped')
        second = supervisor.resume()['session']
        self.assertNotEqual(first, second)
        supervisor.stop()
        self.assertEqual(supervisor.wait(2)['state'], 'stopped')

    def test_backend_failure_is_visible(self):
        def backend(*args):
            raise RuntimeError('offline')
        supervisor = CampaignSupervisor(Config(), backend)
        supervisor.start()
        status = supervisor.wait(2)
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['error'], 'offline')

    def test_fatal_result_is_not_success(self):
        supervisor = CampaignSupervisor(Config(), lambda *args: {'runs': 0, 'fatal': True})
        supervisor.start()
        self.assertEqual(supervisor.wait(2)['state'], 'failed')

    def test_invalid_parameters_do_not_start_backend(self):
        supervisor = CampaignSupervisor(Config(), lambda *args: self.fail('started'))
        for arguments in ({'iterations': True}, {'seconds': float('nan')}, {'seconds': 0},
                          {'seed': -1}, {'families': 'hid'}, {'profiles': [3]}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                supervisor.start(**arguments)
        self.assertEqual(supervisor.status()['state'], 'idle')

    def test_idle_stop_and_resume_without_history(self):
        supervisor = CampaignSupervisor(Config())
        self.assertEqual(supervisor.stop()['state'], 'idle')
        with self.assertRaises(RuntimeError):
            supervisor.resume()


if __name__ == '__main__':
    unittest.main()
