import hashlib
import http.client
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from usb_device_lab.dashboard import DashboardServer, main, result_summaries

TOKEN = 'd' * 48


class DashboardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
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

    def database(self, result):
        path = self.root / 'runs.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE runs(id TEXT,status TEXT,started REAL,digest TEXT,result TEXT)')
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?)', ('a'*32, 'error', 1.0, 'digest', result))
        return path

    def test_navigation_and_assets_have_no_credentials(self):
        for path in ('/', '/events', '/results'):
            code, body = self.get(path, authenticated=False)
            self.assertEqual(code, 200)
            for link in (b'href="/"', b'href="/events"', b'href="/results"'):
                self.assertIn(link, body)
            self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(self.get('/results.js', authenticated=False)[0], 200)

    def test_results_requires_auth_and_valid_host(self):
        self.assertEqual(self.get('/api/results', authenticated=False)[0], 401)
        self.assertEqual(self.get('/api/results', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.get('/api/results', headers={'Origin': 'https://evil.test'})[0], 403)

    def test_empty_results_does_not_create_database(self):
        code, body = self.get('/api/results')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), [])
        self.assertFalse((self.root / 'runs.sqlite3').exists())

    def test_summary_preserves_unconfirmed_verdict_without_raw_result(self):
        path = self.database(json.dumps({'new_pcs': 2, 'verdict': {'outcome': 'kernel_candidate', 'confirmation': 'unknown'}, 'executor_log': 'private log'}))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        code, body = self.get('/api/results?offset=0')
        self.assertEqual(code, 200)
        row = json.loads(body)[0]
        self.assertEqual(row['outcome'], 'kernel_candidate')
        self.assertEqual(row['confirmation'], 'unknown')
        self.assertEqual(row['new_pcs'], 2)
        self.assertNotIn(b'private log', body)
        self.assertNotIn('result', row)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_invalid_queries_rejected(self):
        for query in ('offset=-1', 'offset=', 'offset=1&offset=2', 'unknown=1', 'offset=10000001'):
            self.assertEqual(self.get('/api/results?' + query)[0], 400)

    def test_oversized_summary_is_inconclusive(self):
        self.database('x' * (2 * 1024 * 1024 + 1))
        row = result_summaries(self.root)[0]
        self.assertEqual(row['outcome'], 'inconclusive')
        self.assertTrue(row['summary_limited'])

    def test_lab_commands_forwarded_unchanged(self):
        for command in ('init', 'check', 'fuzz', 'replay'):
            delegate = Mock(return_value=7)
            arguments = [command, '--help']
            self.assertEqual(main(arguments, lab_main=delegate), 7)
            delegate.assert_called_once_with(arguments)

    def test_serve_loads_config_and_validates_port(self):
        config = SimpleNamespace(results_dir=self.root)
        serve = Mock(return_value=0)
        with patch('usb_device_lab.lab.load', return_value=config) as load:
            self.assertEqual(main(['serve', '--config', 'example.toml', '--port', '9000'], serve=serve), 0)
            load.assert_called_once_with('example.toml')
        serve.assert_called_once_with(config, 9000)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['serve', '--port', '0'], serve=serve)
        self.assertEqual(serve.call_count, 1)


if __name__ == '__main__':
    unittest.main()
