import io
import signal
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
from usb_device_lab.uart_campaign import main

class UARTCLIPreflightTests(unittest.TestCase):
    def invoke(self, report=None, load_error=None, check_error=None, extra=()):
        before = {sig:signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        config = object()
        with patch('usb_device_lab.uart_config.load', return_value=config, side_effect=load_error) as loader, patch('usb_device_lab.uart_preflight.inspect', return_value=report, side_effect=check_error) as check, patch('usb_device_lab.uart_campaign.run_campaign', return_value={'outcomes':[]}) as run, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            try:
                code = main(['--config','lab.uart.toml','--seeds','seed.json',*extra])
            except SystemExit as error:
                code = error.code
            self.assertEqual({sig:signal.getsignal(sig) for sig in before}, before)
            return code, out.getvalue(), err.getvalue(), run, check, config

    def test_failed_metadata_never_calls_campaign(self):
        code, out, err, run, check, config = self.invoke({'metadata_checks_passed':False,'checks':[{'name':'udc','passed':False}]})
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertEqual(out, '')
        self.assertIn('udc', err)
        check.assert_called_once_with(config, ['seed.json'])

    def test_success_uses_loaded_config_and_resume(self):
        code, out, err, run, check, config = self.invoke({'metadata_checks_passed':True}, extra=['--resume','old/checkpoint.json'])
        self.assertEqual(code, 0)
        self.assertIs(run.call_args.args[0], config)
        self.assertEqual(run.call_args.args[1], ['seed.json'])
        self.assertEqual(run.call_args.kwargs['resume'], 'old/checkpoint.json')
        self.assertIn('unknown', err)
        self.assertIn('outcomes', out)

    def test_config_error_before_check_or_campaign(self):
        code, out, err, run, check, config = self.invoke(load_error=ValueError('bad config'))
        self.assertEqual(code, 2)
        run.assert_not_called()
        check.assert_not_called()

    def test_inspection_error_before_campaign(self):
        code, out, err, run, check, config = self.invoke(check_error=OSError('permission denied'))
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn('permission denied', err)

    def test_truthy_integer_not_a_pass(self):
        code, out, err, run, check, config = self.invoke({'metadata_checks_passed':1})
        self.assertEqual(code, 1)
        run.assert_not_called()

    def test_cancel_during_preflight_does_not_open_campaign(self):
        def inspect(config, seeds):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return {'metadata_checks_passed':True}
        code, out, err, run, check, config = self.invoke(check_error=inspect)
        self.assertEqual(code, 130)
        run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
