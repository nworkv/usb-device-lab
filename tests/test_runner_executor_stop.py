import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from usb_device_lab.runner import terminate


class ExecutorStopTests(unittest.TestCase):
    def process(self):
        process = Mock(pid=4242, args=['executor'])
        process.poll.return_value = None
        return process

    def test_already_exited_is_reaped_without_signals(self):
        process = self.process()
        process.poll.return_value = 0
        with patch('usb_device_lab.runner.os.killpg') as send:
            report = terminate(process)
        send.assert_not_called()
        process.wait.assert_called_once_with(timeout=0)
        self.assertTrue(report['reaped'])

    def test_term_success_does_not_send_kill(self):
        process = self.process()
        with patch('usb_device_lab.runner.os.killpg') as send:
            report = terminate(process, term_timeout=8)
        send.assert_called_once_with(4242, signal.SIGTERM)
        self.assertEqual(report['signals'], ['SIGTERM'])
        self.assertFalse(report['forced'])
        self.assertTrue(report['reaped'])

    def test_term_timeout_escalates_and_waits(self):
        process = self.process()
        process.wait.side_effect = [subprocess.TimeoutExpired('executor', 8), -9]
        with patch('usb_device_lab.runner.os.killpg') as send:
            report = terminate(process, term_timeout=8)
        self.assertEqual([c.args[1] for c in send.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual([c.kwargs['timeout'] for c in process.wait.call_args_list], [8, 8])
        self.assertTrue(report['forced'])
        self.assertTrue(report['reaped'])

    def test_exit_race_still_waits(self):
        process = self.process()
        with patch('usb_device_lab.runner.os.killpg', side_effect=ProcessLookupError):
            report = terminate(process)
        self.assertTrue(report['reaped'])

    def test_unreapable_process_is_not_reported_as_success(self):
        process = self.process()
        process.wait.side_effect = subprocess.TimeoutExpired('executor', 8)
        with patch('usb_device_lab.runner.os.killpg'):
            with self.assertRaisesRegex(RuntimeError, 'did not exit after SIGKILL'):
                terminate(process)

    def test_permission_error_is_not_hidden(self):
        with patch('usb_device_lab.runner.os.killpg', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                terminate(self.process())

    def test_real_child_ignoring_term_is_killed_and_reaped(self):
        code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
        process = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            report = terminate(process, term_timeout=0.1, kill_timeout=2)
            self.assertTrue(report['forced'])
            self.assertTrue(report['reaped'])
            self.assertEqual(process.returncode, -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
