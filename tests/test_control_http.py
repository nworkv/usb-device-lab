import http.client
import json
import threading
import unittest
from unittest.mock import Mock
from usb_device_lab.control_http import ControlServer

TOKEN = 'x' * 48


class ControlHTTPTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = Mock()
        self.supervisor.status.return_value = {'state': 'idle'}
        self.supervisor.start.return_value = {'state': 'starting'}
        self.supervisor.stop.return_value = {'state': 'stopping'}
        self.supervisor.resume.return_value = {'state': 'starting'}
        self.server = ControlServer(self.supervisor, TOKEN, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        defaults = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
        if headers:
            defaults.update(headers)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body, defaults)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_status_requires_token(self):
        code, body, headers = self.request('GET', '/api/campaign/status', headers={'Authorization': ''})
        self.assertEqual(code, 401)
        self.supervisor.status.assert_not_called()
        self.assertEqual(self.request('GET', '/api/campaign/status')[0], 200)

    def test_page_has_no_token_and_security_headers(self):
        code, body, headers = self.request('GET', '/', headers={'Authorization': ''})
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('GET', '/control.js')[0], 200)

    def test_start_forwards_only_explicit_fields(self):
        data = {'iterations': 4, 'seconds': 2, 'families': ['hid']}
        self.assertEqual(self.request('POST', '/api/campaign/start', json.dumps(data))[0], 202)
        self.supervisor.start.assert_called_once_with(**data)

    def test_stop_and_resume(self):
        for action in ('stop', 'resume'):
            self.assertEqual(self.request('POST', '/api/campaign/' + action, '{}')[0], 202)
            getattr(self.supervisor, action).assert_called_once_with()

    def test_host_and_origin_rejected_before_action(self):
        for headers in ({'Host': 'evil.test'}, {'Origin': 'https://evil.test'}, {'Origin': 'null'}, {'Authorization': 'bad'}):
            self.assertIn(self.request('POST', '/api/campaign/start', '{}', headers)[0], (401, 403))
        self.supervisor.start.assert_not_called()

    def test_malformed_json_and_unknown_parameters(self):
        for body in ('[]', '{', '{"seed":1,"seed":2}', '{"seconds":NaN}', '{"command":"id"}'):
            self.assertEqual(self.request('POST', '/api/campaign/start', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/campaign/stop', '{"seed":1}')[0], 400)
        self.supervisor.start.assert_not_called()
        self.supervisor.stop.assert_not_called()

    def test_body_limit_and_content_type(self):
        self.assertEqual(self.request('POST', '/api/campaign/start', ' ' * 16385)[0], 413)
        self.assertEqual(self.request('POST', '/api/campaign/start', '{}', {'Content-Type': 'text/plain'})[0], 415)
        self.supervisor.start.assert_not_called()

    def test_conflict_validation_and_internal_error(self):
        for error, expected in ((RuntimeError('already running'), 409), (ValueError('bad seed'), 400), (OSError('private path'), 500)):
            self.supervisor.start.side_effect = error
            code, body, _ = self.request('POST', '/api/campaign/start', '{}')
            self.assertEqual(code, expected)
            if expected == 500:
                self.assertNotIn(b'private path', body)

    def test_unknown_route_and_shutdown(self):
        self.assertEqual(self.request('POST', '/api/campaign/delete', '{}')[0], 404)
        self.server.closing = True
        self.assertEqual(self.request('POST', '/api/campaign/start', '{}')[0], 503)
        self.supervisor.start.assert_not_called()

    def test_weak_token_rejected(self):
        with self.assertRaises(ValueError):
            ControlServer(self.supervisor, 'weak', 0)


if __name__ == '__main__':
    unittest.main()
