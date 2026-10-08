"""Loopback-only campaign control panel; all API requests require a bearer token."""
import argparse
import hmac
import json
import os
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>USB Device Lab control</title><h1>Campaign control</h1>
<label>Token <input id="token" type="password" autocomplete="off"></label>
<label>Iterations <input id="iterations" type="number" value="100" min="1"></label>
<label>Seconds <input id="seconds" type="number" value="5" min="0.1" step="0.1"></label>
<label>Seed <input id="seed" type="number" value="0" min="0"></label>
<button id="start">Start</button><button id="stop">Stop</button>
<button id="resume">Resume</button><button id="status">Status</button>
<p>Stop finishes the current attempt. Resume starts a new session with the existing corpus.</p>
<pre id="result"></pre><script src="/control.js"></script></html>
"""
SCRIPT = b"""'use strict';
const el = id => document.getElementById(id);
async function request(action) {
  const status = action === 'status';
  const options = {method: status ? 'GET' : 'POST',
    headers: {'Authorization': 'Bearer ' + el('token').value}};
  if (!status) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(action === 'start' ? {
      iterations: Number(el('iterations').value), seconds: Number(el('seconds').value),
      seed: Number(el('seed').value)} : {});
  }
  try {
    const response = await fetch('/api/campaign/' + action, options);
    el('result').textContent = JSON.stringify(await response.json(), null, 2);
  } catch (error) { el('result').textContent = String(error); }
}
for (const action of ['start', 'stop', 'resume', 'status'])
  el(action).addEventListener('click', () => request(action));
"""


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON number')


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, supervisor, token, port=8765):
        if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('Token must contain at least 32 non-whitespace ASCII characters')
        self.supervisor = supervisor
        self.token = token
        self.closing = False
        self.action_lock = threading.RLock()
        super().__init__(('127.0.0.1', port), ControlHandler)
        self.expected_host = '127.0.0.1:' + str(self.server_port)
        self.expected_origin = 'http://' + self.expected_host


class ControlHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Never log credentials or request bodies.

    def reply(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def guard(self, authenticated=True):
        if self.headers.get_all('Host') != [self.server.expected_host]:
            self.reply(403, {'error': 'Invalid Host'})
            return False
        origins = self.headers.get_all('Origin')
        if origins is not None and origins != [self.server.expected_origin]:
            self.reply(403, {'error': 'Invalid Origin'})
            return False
        if authenticated:
            auth = self.headers.get_all('Authorization') or []
            expected = 'Bearer ' + self.server.token
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected.encode('ascii')):
                self.reply(401, {'error': 'Unauthorized'})
                return False
        if self.server.closing:
            self.reply(503, {'error': 'Server is shutting down'})
            return False
        return True

    def do_GET(self):
        public = self.path in ('/', '/control.js')
        if not self.guard(authenticated=not public):
            return
        if self.path == '/':
            self.reply(200, PAGE, 'text/html; charset=utf-8')
        elif self.path == '/control.js':
            self.reply(200, SCRIPT, 'text/javascript; charset=utf-8')
        elif self.path == '/api/campaign/status':
            self.reply(200, self.server.supervisor.status())
        else:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.guard():
            return
        actions = {'/api/campaign/start': 'start', '/api/campaign/stop': 'stop', '/api/campaign/resume': 'resume'}
        if self.path not in actions:
            self.reply(404, {'error': 'Not found'})
            return
        try:
            if self.headers.get('Transfer-Encoding') is not None:
                raise ValueError('Transfer-Encoding is unsupported')
            lengths = self.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError('One numeric Content-Length is required')
            length = int(lengths[0])
            if length > 16384:
                self.reply(413, {'error': 'Request body too large'})
                return
            if self.headers.get_content_type() != 'application/json':
                self.reply(415, {'error': 'application/json required'})
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('Incomplete request body')
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=strict_object, parse_constant=reject_constant)
            if type(data) is not dict:
                raise ValueError('JSON object required')
            action = actions[self.path]
            allowed = {'iterations', 'seconds', 'seed', 'families', 'profiles'} if action == 'start' else set()
            if set(data) - allowed:
                raise ValueError('Unsupported request fields')
            with self.server.action_lock:
                if self.server.closing:
                    self.reply(503, {'error': 'Server is shutting down'})
                    return
                response = getattr(self.server.supervisor, action)(**data)
        except (ValueError, UnicodeError, TimeoutError):
            self.reply(400, {'error': 'Invalid request or campaign parameters'})
            return
        except RuntimeError as error:
            self.reply(409, {'error': str(error)[:4096]})
            return
        except Exception:
            self.reply(500, {'error': 'Internal control error'})
            return
        self.reply(202, response)


def main(argv=None):
    from .lab import load
    from .control import CampaignSupervisor
    parser = argparse.ArgumentParser(description='Local USB Device Lab campaign control panel')
    parser.add_argument('--config', default='lab.toml')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    token = os.environ.get('USB_DEVICE_LAB_CONTROL_TOKEN') or secrets.token_urlsafe(32)
    supervisor = CampaignSupervisor(load(args.config))
    server = ControlServer(supervisor, token, args.port)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    print('Control panel: ' + server.expected_origin, flush=True)
    print('Local bearer token: ' + token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with server.action_lock:
            server.closing = True
            supervisor.stop()
        server.server_close()
        supervisor.wait()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
