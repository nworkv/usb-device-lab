import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from usb_device_lab.uart_guarded_web import GuardedSupervisor

class Base:
    def __init__(self):
        self.config = object()
        self.seeds = ('seed.json',)
        self.lock = threading.RLock()
        self.closing, self.thread = False, None
        self.commands = []
    def status(self):
        return {'state':'idle'}
    def start(self, campaign=None):
        self.commands.append(('start',campaign))
    def stop(self):
        self.commands.append(('stop',None))
    def close(self):
        self.closing = True
        self.commands.append(('close',None))

class UARTGuardedWebTests(unittest.TestCase):
    def setUp(self):
        self.base = Base()

    def test_rejection_preserves_report_and_never_launches(self):
        report = {'metadata_checks_passed':False,'hardware_checked':False,'checks':[{'name':'udc','passed':False}]}
        checker = Mock(return_value=report)
        wrapper = GuardedSupervisor(self.base, checker)
        with self.assertRaises(RuntimeError):
            wrapper.start()
        self.assertEqual(self.base.commands, [])
        self.assertEqual(wrapper.status()['preflight'], report)
        checker.assert_called_once_with(self.base.config, ['seed.json'])

    def test_success_and_resume_forwarding(self):
        checker = Mock(return_value={'metadata_checks_passed':True,'hardware_checked':False})
        wrapper = GuardedSupervisor(self.base, checker)
        wrapper.start('a'*32)
        wrapper.start()
        self.assertEqual(self.base.commands, [('start','a'*32),('start',None)])
        self.assertEqual(checker.call_count, 2)
        self.assertIs(wrapper.config, self.base.config)

    def test_busy_or_closing_before_inspection(self):
        checker = Mock()
        wrapper = GuardedSupervisor(self.base, checker)
        for closing, thread in [(True,None),(False,SimpleNamespace(is_alive=lambda:True))]:
            self.base.closing, self.base.thread = closing, thread
            with self.assertRaises(RuntimeError):
                wrapper.start()
        checker.assert_not_called()

    def test_inspection_error_is_visible_without_launch(self):
        wrapper = GuardedSupervisor(self.base, Mock(side_effect=OSError('permission denied')))
        with self.assertRaises(RuntimeError):
            wrapper.start()
        self.assertIn('permission denied', wrapper.status()['preflight']['inspection_error'])
        self.assertEqual(self.base.commands, [])

    def test_malformed_or_truthy_report_rejected(self):
        for report in (None, [], {'metadata_checks_passed':1}):
            wrapper = GuardedSupervisor(self.base, Mock(return_value=report))
            with self.assertRaises(RuntimeError):
                wrapper.start()
        self.assertEqual(self.base.commands, [])

    def test_stop_during_preflight_prevents_launch(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def checker(config, seeds):
            entered.set()
            release.wait(2)
            return {'metadata_checks_passed':True}
        wrapper = GuardedSupervisor(self.base, checker)
        def launch():
            try:
                wrapper.start()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=launch)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(wrapper.status()['preflight_pending'])
            with self.assertRaises(RuntimeError):
                wrapper.start()
            wrapper.stop()
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.base.commands, [('stop',None)])
            self.assertTrue(any('canceled' in e for e in errors))
        finally:
            release.set()
            thread.join(2)

    def test_status_isolated_and_stop_close_delegate(self):
        report = {'metadata_checks_passed':True,'checks':[]}
        wrapper = GuardedSupervisor(self.base, Mock(return_value=report))
        wrapper.start()
        view = wrapper.status()
        view['preflight']['checks'].append('modified')
        self.assertEqual(wrapper.status()['preflight']['checks'], [])
        wrapper.stop()
        wrapper.close()
        self.assertEqual(self.base.commands[-2:], [('stop',None),('close',None)])

if __name__ == '__main__':
    unittest.main()
