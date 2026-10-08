import http.client
import json
import threading
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from unittest.mock import Mock
from usb_device_lab.control_http import ControlServer
from usb_device_lab.panel_ui import PAGE, STYLE, SCRIPT

TOKEN = 'p' * 48


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
    def handle_starttag(self, tag, attributes):
        value = dict(attributes).get('id')
        if value is not None:
            self.ids.append(value)


class PanelUITests(unittest.TestCase):
    def setUp(self):
        self.supervisor = Mock()
        self.supervisor.status.return_value = {'state': 'idle', 'runs': 0, 'session': None}
        self.supervisor.start.return_value = {'state': 'starting', 'runs': 0, 'session': 'test'}
        self.server = ControlServer(self.supervisor, TOKEN, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_russian_page_and_unique_control_ids(self):
        code, body, _ = self.request('GET', '/')
        self.assertEqual(code, 200)
        text = body.decode('utf-8')
        self.assertIn('lang="ru"', text)
        self.assertIn('Запустить', text)
        self.assertIn('Остановить', text)
        parser = IdParser()
        parser.feed(text)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for identifier in ('token', 'iterations', 'seconds', 'seed', 'start', 'stop', 'resume', 'status', 'result', 'notice', 'state-label', 'run-count', 'session-label', 'auto-status'):
            self.assertIn(identifier, parser.ids)

    def test_assets_and_csp_without_inline_permission(self):
        for path, content_type in (('/control.css', 'text/css'), ('/control.js', 'text/javascript')):
            code, body, headers = self.request('GET', path)
            self.assertEqual(code, 200)
            self.assertIn(content_type, headers['Content-Type'])
            self.assertIn("style-src 'self'", headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertNotIn(TOKEN.encode(), body)
        self.assertIn(b'backdrop-filter', STYLE)

    def test_api_still_requires_authorization(self):
        self.assertEqual(self.request('GET', '/api/campaign/status')[0], 401)
        self.supervisor.status.assert_not_called()

    def test_authenticated_status_and_start_compatible(self):
        headers = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
        code, body, _ = self.request('GET', '/api/campaign/status', headers=headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['state'], 'idle')
        self.assertEqual(self.request('POST', '/api/campaign/start', '{"iterations":2}', headers)[0], 202)
        self.supervisor.start.assert_called_once_with(iterations=2)

    def test_asset_host_and_origin_checks_preserved(self):
        for headers in ({'Host': 'evil.test'}, {'Origin': 'https://evil.test'}):
            self.assertEqual(self.request('GET', '/control.css', headers=headers)[0], 403)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the JavaScript contract test')
    def test_javascript_request_validation_and_russian_status(self):
        harness = r"""
const vm = require('vm'), assert = require('assert');
const elements = Object.create(null), calls = [];
function element(id) {
  if (!elements[id]) elements[id] = {value:'', checked:false, textContent:'', dataset:{}, disabled:false, addEventListener(){}};
  return elements[id];
}
const context = {document:{getElementById:element}, setInterval(){},
  fetch:async (url, options) => { calls.push({url, options}); return {ok:true,status:200,
    json:async()=>({state:'running',runs:1,session:'<unsafe>'})}; }};
vm.createContext(context);
vm.runInContext(SOURCE, context);
(async () => {
  element('token').value = 'local-token';
  element('iterations').value = '0'; element('seconds').value = '5'; element('seed').value = '0';
  await context.request('start'); assert.equal(calls.length,0); assert.equal(element('notice').dataset.error,'true');
  element('iterations').value = '2'; await context.request('start');
  assert.equal(calls.length,1); assert.equal(calls[0].options.headers.Authorization,'Bearer local-token');
  assert.equal(element('state-label').textContent,'Кампания выполняется');
  assert.equal(element('session-label').textContent,'<unsafe>');
  assert.equal(element('start').disabled,false);
  element('seed').value = '9007199254740992'; await context.request('start'); assert.equal(calls.length,1);
  element('token').value = ''; await context.request('status'); assert.equal(calls.length,1);
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        harness = harness.replace('SOURCE', json.dumps(SCRIPT.decode('utf-8')))
        result = subprocess.run([shutil.which('node'), '-e', harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_assets_do_not_store_token_or_use_html_injection(self):
        self.assertNotIn(b'localStorage', SCRIPT)
        self.assertNotIn(b'sessionStorage', SCRIPT)
        self.assertNotIn(b'innerHTML', SCRIPT)
        self.assertIn(b'textContent', SCRIPT)
        self.assertNotIn(b'https://', PAGE)
        self.assertNotIn(TOKEN.encode(), PAGE)


if __name__ == '__main__':
    unittest.main()
