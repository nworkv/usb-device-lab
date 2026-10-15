"""Guarded adapter + actual loopback HTTP server; campaign/checker are test doubles."""
import http.client
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from usb_device_lab.uart_web import Server
from usb_device_lab.uart_guarded_web import GuardedSupervisor

TOKEN = 'guarded-http-' + 'x' * 40

class Base:
    def __init__(self):
        self.config = SimpleNamespace()
        self.seeds = ('seed.json',)
        self.lock = threading.RLock()
        self.thread, self.closing, self.commands = None, False, []
    def status(self):
        with self.lock:
            return {'state':'idle', 'commands':list(self.commands)}
    def start(self, campaign=None):
        with self.lock:
            self.commands.append(['start',campaign])
    def stop(self):
        with self.lock:
            self.commands.append(['stop',None])
    def close(self):
        self.closing = True

class UARTGuardedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.base = Base()
        self.checker = Mock(return_value={'metadata_checks_passed':False,'hardware_checked':False,'runtime_connected':False})
        self.guard = GuardedSupervisor(self.base, self.checker)
        self.server = Server(self.guard, TOKEN, 0)
        self.releases, self.clients = [], []
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.01})
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.guard.stop()
        for event in self.releases:
            event.set()
        for thread in self.clients:
            thread.join(3)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.guard.close()

    def request(self, path, body=None, auth=True, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type':'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer '+TOKEN
        if origin:
            headers['Origin'] = origin
        try:
            connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_failed_check_returns_conflict_and_visible_status(self):
        code, data = self.request('/api/start', {})
        self.assertEqual(code, 409)
        self.assertEqual(self.base.commands, [])
        code, data = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertFalse(data['preflight']['metadata_checks_passed'])
        self.assertFalse(data['preflight_pending'])
        self.assertEqual(data['preflight_gate'], 'guarded_web_launcher')

    def test_no_token_or_wrong_origin_never_checks_or_launches(self):
        self.assertIn(self.request('/api/start', {}, auth=False)[0], (401,403))
        self.assertEqual(self.request('/api/start', {}, origin='http://evil.example')[0], 403)
        self.checker.assert_not_called()
        self.assertEqual(self.base.commands, [])

    def test_success_and_resume_are_checked_separately(self):
        self.checker.return_value = {'metadata_checks_passed':True,'hardware_checked':False}
        self.assertEqual(self.request('/api/start', {})[0], 202)
        self.assertEqual(self.request('/api/resume', {'campaign':'a'*32})[0], 202)
        self.assertEqual(self.base.commands, [['start',None],['start','a'*32]])
        self.assertEqual(self.checker.call_count, 2)
        self.checker.assert_called_with(self.base.config, ['seed.json'])

    def test_inspection_exception_is_conflict_not_campaign_start(self):
        self.checker.side_effect = OSError('fixture inspection error')
        self.assertEqual(self.request('/api/start', {})[0], 409)
        code, data = self.request('/api/state')
        self.assertIn('fixture inspection error', data['preflight']['inspection_error'])
        self.assertEqual(self.base.commands, [])

    def test_stop_while_http_preflight_is_pending(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        responses, failures = [], []
        def check(config, seeds):
            entered.set()
            if not release.wait(2):
                raise OSError('Test did not release preflight')
            return {'metadata_checks_passed':True,'hardware_checked':False}
        self.checker.side_effect = check
        def start():
            try:
                responses.append(self.request('/api/start', {}))
            except Exception as error:
                failures.append(str(error))
        client = threading.Thread(target=start)
        self.clients.append(client)
        client.start()
        self.assertTrue(entered.wait(1))
        code, data = self.request('/api/state')
        self.assertTrue(data['preflight_pending'])
        self.assertEqual(self.request('/api/start', {})[0], 409)
        self.assertEqual(self.request('/api/stop', {})[0], 202)
        release.set()
        client.join(3)
        self.assertFalse(client.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(responses[0][0], 409)
        self.assertEqual(self.base.commands, [['stop',None]])
        self.assertEqual(self.checker.call_count, 1)

if __name__ == '__main__':
    unittest.main()
