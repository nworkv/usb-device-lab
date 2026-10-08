"""HTTP regression tests for the loopback-only read-only dashboard."""
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from usb_device_lab.web import MAX_ARTIFACT_BYTES, make_server, run_view


class WebTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ident = 'a' * 32
        self.directory = self.root / 'runs' / self.ident
        self.directory.mkdir(parents=True)
        (self.directory / 'config.json').write_text('{}', encoding='utf-8')
        (self.directory / 'metadata.json').write_text('{}', encoding='utf-8')
        with sqlite3.connect(self.root / 'runs.sqlite3') as db:
            db.executescript("""
                CREATE TABLE runs(id TEXT PRIMARY KEY,started REAL,finished REAL,digest TEXT,status TEXT,result TEXT);
                CREATE TABLE errors(signature TEXT PRIMARY KEY,kind TEXT,first_run TEXT,last_run TEXT,occurrences INTEGER);
            """)
            result = {'new_pcs': 2, 'kernel_events': [], 'verdict': {
                'outcome': 'new_coverage', 'confirmation': 'not_applicable',
                'kernel_evidence_detected': False, 'telemetry_complete': True,
            }}
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',
                       (self.ident, 1, 2, 'digest', 'ok', json.dumps(result)))
        self.server = make_server(self.root, port=0)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={'poll_interval': 0.01}, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def request(self, path, method='GET', headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_run_list_has_verdict_without_full_result(self):
        status, _, body = self.request('/api/runs')
        self.assertEqual(status, 200)
        rows = json.loads(body)
        self.assertEqual(rows[0]['verdict']['outcome'], 'new_coverage')
        self.assertEqual(rows[0]['new_pcs'], 2)
        self.assertNotIn('result', rows[0])

    def test_run_detail_lists_existing_artifacts(self):
        status, _, body = self.request('/api/run/' + self.ident)
        self.assertEqual(status, 200)
        item = json.loads(body)
        self.assertEqual(item['metadata'], {})
        self.assertIn('config.json', item['artifacts'])
        self.assertNotIn('verdict.json', item['artifacts'])

    def test_config_alias_and_artifact_download(self):
        for path in ('/api/config/' + self.ident,
                     '/api/artifact/' + self.ident + '/config.json'):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'application/json')
                self.assertEqual(body, b'{}')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_unknown_artifact_is_denied_even_if_it_exists(self):
        (self.directory / 'private.json').write_text('secret', encoding='utf-8')
        status, _, _ = self.request('/api/artifact/' + self.ident + '/private.json')
        self.assertEqual(status, 404)

    def test_symlink_outside_run_is_denied(self):
        outside = self.root / 'secret.json'
        outside.write_text('secret', encoding='utf-8')
        (self.directory / 'verdict.json').symlink_to(outside)
        status, _, _ = self.request('/api/artifact/' + self.ident + '/verdict.json')
        self.assertEqual(status, 404)

    def test_oversized_artifact_is_rejected(self):
        with open(self.directory / 'executor.log', 'wb') as stream:
            stream.truncate(MAX_ARTIFACT_BYTES + 1)
        status, _, _ = self.request('/api/artifact/' + self.ident + '/executor.log')
        self.assertEqual(status, 413)

    def test_untrusted_host_header_is_denied(self):
        status, _, _ = self.request('/api/runs', headers={'Host': 'untrusted.invalid'})
        self.assertEqual(status, 403)

    def test_invalid_offset_returns_bad_request(self):
        status, _, _ = self.request('/api/runs?offset=invalid')
        self.assertEqual(status, 400)

    def test_legacy_errors_are_not_automatically_kernel_findings(self):
        view = run_view({'status': 'error', 'result': json.dumps({
            'errors': [{'kind': 'kernel', 'summary': 'old diagnostic'}],
        })})
        self.assertEqual(view['verdict']['outcome'], 'legacy_unclassified')
        self.assertIsNone(view['verdict']['kernel_evidence_detected'])
        self.assertEqual(view['verdict']['confirmation'], 'unknown')

    def test_interrupted_detail_reads_saved_verdict(self):
        verdict = {'outcome': 'interrupted', 'confirmation': 'unknown',
                   'kernel_evidence_detected': None, 'telemetry_complete': False}
        (self.directory / 'verdict.json').write_text(json.dumps(verdict), encoding='utf-8')
        with sqlite3.connect(self.root / 'runs.sqlite3') as db:
            db.execute("UPDATE runs SET status='interrupted',result=NULL WHERE id=?", (self.ident,))
        status, _, body = self.request('/api/run/' + self.ident)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['verdict'], verdict)

    def test_post_does_not_modify_database(self):
        status, _, _ = self.request('/api/runs', method='POST')
        self.assertEqual(status, 501)
        with sqlite3.connect(self.root / 'runs.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
