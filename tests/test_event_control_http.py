import http.client
import json
import threading
import unittest
from unittest.mock import Mock
from usb_device_lab.event_control import EventControlServer

TOKEN = 't' * 48


class EventHTTPTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = Mock()
        self.supervisor.events.return_value = {'events': [], 'next': 0, 'gap': False}
        self.server = EventControlServer(self.supervisor, TOKEN, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def get(self, path, headers=None):
        values = {'Authorization': 'Bearer ' + TOKEN}
        values.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request('GET', path, headers=values)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_cursor_forwarded(self):
        code, body = self.get('/api/campaign/events?after=7&limit=2')
        self.assertEqual(code, 200)
        self.supervisor.events.assert_called_once_with(7, 2)
        self.assertEqual(json.loads(body)['next'], 0)

    def test_authentication_and_origin(self):
        for headers in ({'Authorization': ''}, {'Origin': 'https://evil.test'}, {'Host': 'evil.test'}):
            self.assertIn(self.get('/api/campaign/events', headers)[0], (401, 403))
        self.supervisor.events.assert_not_called()

    def test_invalid_queries_and_cursor(self):
        for query in ('after=-1', 'after=1&after=2', 'unknown=1', 'after=', 'after=0&limit=1&extra=1'):
            self.assertEqual(self.get('/api/campaign/events?' + query)[0], 400)
        self.supervisor.events.assert_not_called()
        self.supervisor.events.side_effect = ValueError('future cursor')
        self.assertEqual(self.get('/api/campaign/events?after=99')[0], 400)

    def test_live_script_and_unavailable_journal(self):
        code, body = self.get('/control.js', {'Authorization': ''})
        self.assertEqual(code, 200)
        self.assertIn(b'pollEvents', body)
        self.assertNotIn(TOKEN.encode(), body)
        self.supervisor.events.side_effect = RuntimeError('closed')
        self.assertEqual(self.get('/api/campaign/events')[0], 503)


if __name__ == '__main__':
    unittest.main()
