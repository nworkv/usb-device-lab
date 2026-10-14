import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.uart_web import Server, artifact, PAGE, SCRIPT, Supervisor as RealSupervisor

TOKEN = 'x' * 40
ID = 'a' * 32

class Supervisor:
    def __init__(self, config):
        self.config = config
        self.commands = []
    def status(self):
        return {'state':'idle'}
    def start(self, campaign=None):
        self.commands.append(('start', campaign))
    def stop(self):
        self.commands.append(('stop', None))

class UARTWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = SimpleNamespace(results_dir=self.root / 'results', logs_dir=self.root / 'logs')
        directory = self.config.results_dir / ID
        directory.mkdir(parents=True)
        (directory / 'campaign.json').write_text(json.dumps({'capture_id':ID,'mode':'uart'}))
        (directory / 'checkpoint.json').write_text('{}')
        self.attempt = directory / 'attempt-000000'
        self.attempt.mkdir()
        (self.attempt / 'result.json').write_text('{"message":"<script>bad</script>"}')
        self.supervisor = Supervisor(self.config)
        self.server = Server(self.supervisor, TOKEN, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, body=None, token=TOKEN, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        opts = {'Content-Type':'application/json'}
        if token is not None:
            opts['Authorization'] = 'Bearer ' + token
        if headers:
            opts.update(headers)
        try:
            conn.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), opts)
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_public_page_api_authentication_and_loopback(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        self.assertEqual(self.request('/', token=None)[0], 200)
        self.assertNotEqual(self.request('/api/state', token=None)[0], 200)
        self.assertEqual(self.request('/api/state', token='bad')[0], 401)
        self.assertEqual(self.request('/api/state')[0], 200)

    def test_host_and_origin_rejected(self):
        self.assertEqual(self.request('/api/state', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/start', {}, headers={'Origin':'http://evil.example'})[0], 403)
        self.assertEqual(self.supervisor.commands, [])

    def test_start_stop_resume_and_no_overrides(self):
        self.assertEqual(self.request('/api/start', {})[0], 202)
        self.assertEqual(self.request('/api/stop', {})[0], 202)
        self.assertEqual(self.request('/api/resume', {'campaign':ID})[0], 202)
        self.assertEqual(self.supervisor.commands, [('start',None),('stop',None),('start',ID)])
        self.assertEqual(self.request('/api/start', {'iterations':99})[0], 400)
        self.assertEqual(self.request('/api/resume', {'campaign':'../file'})[0], 400)

    def test_authenticated_download_and_no_token_url(self):
        q = '/api/file?campaign=' + ID + '&area=attempt-000000&name=result.json'
        code, data, headers = self.request(q)
        self.assertEqual(code, 200)
        self.assertIn(b'<script>', data)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertNotEqual(self.request(q, token=None)[0], 200)
        self.assertNotIn(b'localStorage', SCRIPT)
        self.assertNotIn(b'sessionStorage', SCRIPT)
        self.assertNotIn(b'innerHTML', SCRIPT)
        self.assertIn(b'AbortController', SCRIPT)

    def test_file_allowlist_and_duplicates(self):
        prefix = '/api/file?campaign=' + ID + '&area=attempt-000000&name='
        self.assertEqual(self.request(prefix + '../secret')[0], 400)
        self.assertEqual(self.request(prefix + 'result.json&name=config.json')[0], 400)
        self.assertEqual(self.request('/api/state?token=secret')[0], 404)

    def test_symlink_rejected(self):
        file = self.attempt / 'result.json'
        file.unlink()
        target = self.root / 'outside'
        target.write_text('secret')
        file.symlink_to(target)
        with self.assertRaises(OSError):
            artifact(self.config, ID, 'attempt-000000', 'result.json')

    def test_download_limit_not_truncated(self):
        with patch('usb_device_lab.uart_web.LIMIT', 1):
            self.assertEqual(self.request('/api/file?campaign='+ID+'&area=attempt-000000&name=result.json')[0], 413)

    def test_campaign_inventory(self):
        code, data, _ = self.request('/api/campaigns')
        self.assertEqual(code, 200)
        rows = json.loads(data)
        self.assertEqual(rows[0]['id'], ID)
        self.assertEqual(rows[0]['attempts'], ['attempt-000000'])
        self.assertTrue(rows[0]['checkpoint'])

    def test_busy_error_is_conflict(self):
        with patch.object(self.supervisor, 'start', side_effect=RuntimeError('active')):
            self.assertEqual(self.request('/api/start', {})[0], 409)

    def test_real_supervisor_forwards_resume_and_stops_worker(self):
        import types
        import sys
        calls, ready = [], threading.Event()
        config = SimpleNamespace(**vars(self.config), path=self.root / 'lab.toml', iterations=3, seconds=1, seed=7, uart=SimpleNamespace(port='/dev/test'))
        def run(config, seeds, **kwargs):
            calls.append((seeds, kwargs['resume']))
            ready.set()
            kwargs['stop'].wait(2)
            return {'campaign_id':ID, 'directory':str(config.results_dir / ID), 'outcomes':[]}
        loader = types.ModuleType('usb_device_lab.uart_config')
        loader.load = lambda path: config
        runner = types.ModuleType('usb_device_lab.uart_campaign')
        runner.run_campaign = run
        receiver = types.ModuleType('usb_device_lab.uart_receiver')
        receiver.UARTReceiver = object
        with patch.dict(sys.modules, {loader.__name__:loader, runner.__name__:runner, receiver.__name__:receiver}):
            s = RealSupervisor(config, [self.root / 'seed.json'])
            try:
                s.start(ID)
                self.assertTrue(ready.wait(1))
                with self.assertRaises(RuntimeError):
                    s.start()
                s.stop()
                s.close()
                self.assertEqual(s.status()['state'], 'stopped')
                self.assertEqual(calls[0][1], config.results_dir / ID / 'checkpoint.json')
            finally:
                s.close()

    def test_real_supervisor_rejects_changed_config(self):
        import types
        import sys
        config = SimpleNamespace(**vars(self.config), path=self.root / 'lab.toml', iterations=3, seconds=1, seed=7, uart=SimpleNamespace(port='/dev/test'))
        loader = types.ModuleType('usb_device_lab.uart_config')
        loader.load = lambda path: object()
        runner = types.ModuleType('usb_device_lab.uart_campaign')
        runner.run_campaign = lambda *args, **kwargs: self.fail('Campaign was started')
        receiver = types.ModuleType('usb_device_lab.uart_receiver')
        receiver.UARTReceiver = object
        with patch.dict(sys.modules, {loader.__name__:loader, runner.__name__:runner, receiver.__name__:receiver}):
            s = RealSupervisor(config, [])
            with self.assertRaises(RuntimeError):
                s.start()
            self.assertIsNone(s.thread)

    def test_security_headers_and_script_syntax_source(self):
        code, _, headers = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn(b'/uart.js', PAGE)

if __name__ == '__main__':
    unittest.main()
