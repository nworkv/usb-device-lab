import http.client
import json
import tempfile
import threading
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock
from usb_device_lab.dashboard import DashboardServer
from usb_device_lab.log_panel import LOG_SCRIPT

RUN = 'a' * 32
TOKEN = 'l' * 48


class LogHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        directory = self.root / 'runs' / RUN
        directory.mkdir(parents=True)
        (directory / 'executor.log').write_text('one\ntwo\n')
        self.server = DashboardServer(Mock(), TOKEN, self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def get(self, path, authenticated=True, headers=None):
        values = {'Authorization': 'Bearer ' + TOKEN} if authenticated else {}
        values.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request('GET', path, headers=values)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_list_and_tail_routes(self):
        code, body = self.get('/api/logs/' + RUN)
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 3)
        code, body = self.get('/api/logs/' + RUN + '/executor?lines=1')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['text'], 'two\n')

    def test_auth_host_origin_and_public_shell(self):
        path = '/api/logs/' + RUN
        self.assertEqual(self.get(path, authenticated=False)[0], 401)
        self.assertEqual(self.get(path, headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.get(path, headers={'Origin': 'https://evil.test'})[0], 403)
        code, body = self.get('/logs', authenticated=False)
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(self.get('/logs.js', authenticated=False)[0], 200)

    def test_bad_sources_queries_and_missing_logs(self):
        for suffix in ('/secret', '/executor?lines=0', '/executor?lines=1&lines=2', '/executor?path=/etc/passwd'):
            self.assertEqual(self.get('/api/logs/' + RUN + suffix)[0], 400)
        self.assertEqual(self.get('/api/logs/' + RUN + '/agent')[0], 404)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for JavaScript syntax validation')
    def test_log_script_syntax(self):
        result = subprocess.run([shutil.which('node'), '--check'], input=LOG_SCRIPT.decode('utf-8'),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_arbitrary_paths_or_html_interpretation(self):
        self.assertEqual(self.get('/api/logs/../../etc/passwd')[0], 400)
        code, script = self.get('/logs.js', authenticated=False)
        self.assertEqual(code, 200)
        self.assertNotIn(b'innerHTML', script)
        self.assertIn(b'textContent', script)
        self.assertNotIn(b'localStorage', script)


if __name__ == '__main__':
    unittest.main()
