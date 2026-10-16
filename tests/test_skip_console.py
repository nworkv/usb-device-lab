"""Control and request-boundary tests; no USB or SSH connections."""
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from usb_device_lab import skip_console as console


class SkipConsoleTests(unittest.TestCase):
    def supervisor(self):
        s = console.Supervisor.__new__(console.Supervisor)
        s.lock = threading.RLock()
        s.skip_event = threading.Event()
        s.stop_event = threading.Event()
        s.closing = False
        s.thread = Mock()
        s.thread.is_alive.return_value = True
        s.stage = 'running'
        s.active = '0' * 32
        s.replay_source = None
        return s

    def handler(self, body=b'{}', allowed=True):
        h = console.Handler.__new__(console.Handler)
        h.path = '/api/skip'
        h.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
        h.rfile = io.BytesIO(body)
        h.guard = Mock(return_value=allowed)
        h.reply = Mock()
        h.server = SimpleNamespace(supervisor=self.supervisor())
        h.server.supervisor.status = Mock(return_value={'skip_pending': True})
        return h

    def test_skip_coalesces_pending_requests(self):
        s = self.supervisor()
        s.skip_current()
        self.assertTrue(s.skip_event.is_set())
        with self.assertRaises(RuntimeError):
            s.skip_current()

    def test_skip_rejected_outside_regular_execution(self):
        for field, value in (('stage', 'analyzing'), ('stage', 'idle'), ('closing', True),
                             ('active', None), ('replay_source', {'attempt': 'fixture'})):
            s = self.supervisor()
            setattr(s, field, value)
            with self.assertRaises(RuntimeError):
                s.skip_current()
            self.assertFalse(s.skip_event.is_set())
        s = self.supervisor()
        s.stop_event.set()
        with self.assertRaises(RuntimeError):
            s.skip_current()

    def test_unauthenticated_request_has_no_effect(self):
        h = self.handler(allowed=False)
        h.do_POST()
        self.assertFalse(h.server.supervisor.skip_event.is_set())
        h.reply.assert_not_called()

    def test_valid_request_sets_skip(self):
        h = self.handler()
        h.do_POST()
        self.assertTrue(h.server.supervisor.skip_event.is_set())
        self.assertEqual(h.reply.call_args.args[0], 200)

    def test_invalid_body_is_rejected(self):
        for body in (b'[]', b'null', b'{"extra":1}', b'{', b''):
            h = self.handler(body)
            h.do_POST()
            self.assertEqual(h.reply.call_args.args[0], 400)
            self.assertFalse(h.server.supervisor.skip_event.is_set())

    def test_inactive_campaign_returns_conflict(self):
        h = self.handler()
        h.server.supervisor.stage = 'analyzing'
        h.do_POST()
        self.assertEqual(h.reply.call_args.args[0], 409)

    def test_worker_forwards_skip_event(self):
        s = self.supervisor()
        s.config = object()
        with patch.object(console, 'run_complete', return_value={}) as run:
            s.work(None)
        self.assertIs(run.call_args.kwargs['skip'], s.skip_event)
        self.assertIs(run.call_args.kwargs['stop'], s.stop_event)
        self.assertEqual(s.stage, 'completed')


if __name__ == '__main__':
    unittest.main()
