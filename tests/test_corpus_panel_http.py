import hashlib
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from usb_device_lab.dashboard import DashboardServer
from usb_device_lab.corpus_panel import CORPUS_SCRIPT

TOKEN = 'c' * 48


class CorpusHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.browser = Mock()
        self.browser.list.return_value = {'items': [], 'scanned': 0, 'next_offset': 0, 'more': False}
        self.server = DashboardServer(Mock(), TOKEN, self.root, 0)
        self.server.corpus_browser = self.browser
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

    def test_public_shell_and_protected_data(self):
        code, body = self.get('/corpus', authenticated=False)
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(self.get('/api/corpus', authenticated=False)[0], 401)
        self.assertEqual(self.get('/api/corpus', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.get('/api/corpus', headers={'Origin': 'https://evil.test'})[0], 403)
        self.browser.list.assert_not_called()

    def test_list_filters_forwarded(self):
        self.assertEqual(self.get('/api/corpus?source=learned&offset=5&family=hid&profile=keyboard')[0], 200)
        self.browser.list.assert_called_once_with('learned', 5, 'hid', 'keyboard')

    def test_bad_paths_queries_and_missing_entry(self):
        for path in ('/api/corpus?offset=-1', '/api/corpus?source=seeds&source=learned',
                     '/api/corpus?path=/etc/passwd', '/api/corpus/seeds/../../outside'):
            self.assertEqual(self.get(path)[0], 400)
        self.browser.detail.side_effect = FileNotFoundError('missing')
        self.assertEqual(self.get('/api/corpus/seeds/' + 'a'*64)[0], 404)

    def test_unconfigured_browser_and_safe_script(self):
        self.server.corpus_browser = None
        self.assertEqual(self.get('/api/corpus')[0], 503)
        self.assertEqual(self.get('/corpus.js', authenticated=False)[0], 200)
        self.assertNotIn(b'innerHTML', CORPUS_SCRIPT)
        self.assertNotIn(b'localStorage', CORPUS_SCRIPT)
        self.assertIn(b'textContent', CORPUS_SCRIPT)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for JavaScript syntax validation')
    def test_script_syntax(self):
        result = subprocess.run([shutil.which('node'), '--check'], input=CORPUS_SCRIPT.decode('utf-8'),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
