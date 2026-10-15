"""Loopback-only operator panel for both transports; no browser command injection."""
import hmac
import json
import os
import re
import secrets
import signal
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .dual_config import load
from .dual_pipeline import run_complete
from .dual_ui import PAGE, SCRIPT

IDENT = re.compile(r'[0-9a-f]{32}')
ATTEMPT = re.compile(r'attempt-[0-9]{6}')
PIPELINE = re.compile(r'(?:pipeline|evidence)-[0-9a-f]{32}')
LIMIT = 32 * 1024 * 1024


def artifact(config, ident, area, name, limit=LIMIT):
    if IDENT.fullmatch(ident) is None:
        raise ValueError('Invalid campaign')
    if area == 'campaign':
        allowed = {'campaign.json', 'checkpoint.json', 'selection.json', 'transport-analysis.json'}
        root, parts = config.results_dir, [ident]
    elif area == 'capture':
        allowed = {'uart.raw', 'chunks.jsonl', 'events.jsonl', 'ssh-stderr.log'}
        meta = json.loads(artifact(config, ident, 'campaign', 'campaign.json', 65536))
        if meta.get('mode') not in ('uart', 'ssh') or meta.get('capture_id') != ident:
            raise ValueError('Invalid metadata')
        root, parts = config.logs_dir, [meta['mode'], ident]
    elif ATTEMPT.fullmatch(area):
        allowed = {'config.json', 'started.json', 'window.json', 'result.json', 'executor.log'}
        root, parts = config.results_dir, [ident, area]
    elif PIPELINE.fullmatch(area):
        allowed = {'pipeline-summary.json', 'summary.json', 'complete.json', 'analysis-error.json',
                   'selection.json', 'exports.jsonl', 'reports.jsonl', 'candidates.sqlite3', 'analysis.json'}
        if re.fullmatch(r'report-[0-9]{6}\.json', name):
            allowed.add(name)
        root, parts = config.results_dir, [ident, area]
    else:
        raise ValueError('Invalid artifact area')
    if name not in allowed:
        raise ValueError('Artifact not allowlisted')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Not a regular file')
            if info.st_size > limit:
                raise OverflowError('Artifact exceeds download limit')
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise OverflowError('Artifact grew beyond limit')
            return data
    finally:
        os.close(fd)


def details(config, ident):
    meta = json.loads(artifact(config, ident, 'campaign', 'campaign.json', 65536))
    if meta.get('capture_id') != ident or meta.get('mode') not in ('uart', 'ssh'):
        raise ValueError('Campaign identity mismatch')
    rootfd = os.open(config.results_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(ident, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
        try:
            names = os.listdir(fd)
        finally:
            os.close(fd)
    finally:
        os.close(rootfd)
    attempts, exports = [], []
    for area in sorted(n for n in names if ATTEMPT.fullmatch(n))[-100:]:
        try:
            result = json.loads(artifact(config, ident, area, 'result.json', 1024 * 1024))
        except FileNotFoundError:
            result = None
        attempts.append(dict(id=area, result=result))
    for area in sorted(n for n in names if PIPELINE.fullmatch(n))[-20:]:
        entry = dict(area=area, summary=None, error=None, files=[])
        for filename, key in (('pipeline-summary.json', 'summary'), ('summary.json', 'summary'), ('analysis-error.json', 'error')):
            try:
                entry[key] = json.loads(artifact(config, ident, area, filename, 1024 * 1024))
            except FileNotFoundError:
                pass
        for name in ('pipeline-summary.json', 'summary.json', 'analysis-error.json', 'complete.json',
                     'reports.jsonl', 'exports.jsonl', 'candidates.sqlite3', 'analysis.json'):
            try:
                artifact(config, ident, area, name, 1)
            except OverflowError:
                entry['files'].append(name)
            except FileNotFoundError:
                pass
            else:
                entry['files'].append(name)
        exports.append(entry)
    return dict(metadata=meta, attempts=attempts, exports=exports, causal=False, completeness='unknown')


class Supervisor:
    def __init__(self, config):
        self.config, self.lock = config, threading.RLock()
        self.stop_event, self.thread = threading.Event(), None
        self.stage, self.active, self.receiver = 'idle', None, None
        self.error, self.result, self.closing = None, None, False

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def select(self, mode):
        with self.lock:
            if self.closing or self.busy():
                raise RuntimeError('Cannot switch during execution/analysis')
            self.config = load(self.config.path, mode)
            self.stage, self.active, self.receiver = 'idle', None, None
            self.error, self.result = None, None

    def status(self):
        with self.lock:
            c = self.config
            target = dict(port=c.uart.port, baudrate=c.uart.baudrate) if c.telemetry_mode == 'uart' else dict(host=c.ssh.host, user=c.ssh.user, port=c.ssh.port)
            completed = 0
            if self.active:
                try:
                    completed = json.loads(artifact(c, self.active, 'campaign', 'checkpoint.json', 65536))['next_iteration']
                except (OSError, ValueError, KeyError):
                    pass
            result = {k: v for k, v in (self.result or {}).items() if k != 'outcomes'}
            return dict(mode=c.telemetry_mode, target=target, stage=self.stage, busy=self.busy(),
                active=self.active, completed=completed, iterations=c.iterations,
                capture=self.receiver.status() if self.receiver else None, error=self.error, result=result)

    def start(self, ident=None):
        with self.lock:
            if self.closing or self.busy():
                raise RuntimeError('Campaign or analysis already active')
            if load(self.config.path, self.config.telemetry_mode) != self.config:
                raise RuntimeError('Config changed; reselect mode or restart panel')
            resume = None
            if ident is not None:
                meta = json.loads(artifact(self.config, ident, 'campaign', 'campaign.json', 65536))
                if meta.get('mode') != self.config.telemetry_mode:
                    raise ValueError('Cannot resume using a different transport')
                artifact(self.config, ident, 'campaign', 'checkpoint.json', 65536)
                resume = self.config.results_dir / ident / 'checkpoint.json'
            self.stop_event = threading.Event()
            self.active, self.receiver, self.error, self.result = None, None, None, None
            self.stage = 'preparing'
            self.thread = threading.Thread(target=self.work, args=(resume,), name='dual-operator', daemon=True)
            self.thread.start()

    def work(self, resume):
        def capture(receiver, directory):
            with self.lock:
                self.receiver, self.active = receiver, directory.name
        def stage(value):
            with self.lock:
                self.stage = value
        try:
            result = run_complete(self.config, resume=resume, stop=self.stop_event, on_capture=capture, on_stage=stage)
            with self.lock:
                self.result = result
                self.error = result.get('execution_error') or result.get('analysis_error')
                self.stage = 'failed' if self.error else 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            with self.lock:
                self.error, self.stage = str(error), 'failed'

    def stop(self):
        self.stop_event.set()

    def close(self):
        with self.lock:
            self.closing = True
        self.stop()
        if self.thread:
            self.thread.join(30)
            if self.thread.is_alive():
                raise RuntimeError('Worker still active; inspect stand before retrying')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, value, kind='application/json', filename=None):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers(); self.wfile.write(data)

    def guard(self, auth=True):
        hosts = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if self.headers.get('Host') not in hosts or self.headers.get('Origin') not in (None, *('http://' + h for h in hosts)):
            self.reply(403, {'error': 'Host/Origin rejected'}); return False
        expected = 'Bearer ' + self.server.token
        if auth and not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), expected.encode('ascii')):
            self.reply(401, {'error': 'Token required'}); return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        public = url.path in ('/', '/dual.js')
        if not self.guard(not public):
            return
        try:
            query = parse_qs(url.query, strict_parsing=True, max_num_fields=3)
            if any(len(v) != 1 for v in query.values()):
                raise ValueError('Duplicate parameters')
            c = self.server.supervisor.config
            if url.path == '/' and not query:
                return self.reply(200, PAGE, 'text/html; charset=utf-8')
            if url.path == '/dual.js' and not query:
                return self.reply(200, SCRIPT, 'text/javascript; charset=utf-8')
            if url.path == '/api/state' and not query:
                return self.reply(200, self.server.supervisor.status())
            if url.path == '/api/history' and not query:
                rows = []
                if c.results_dir.exists():
                    for entry in os.scandir(c.results_dir):
                        if IDENT.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                            rows.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name))
                result = []
                for modified, ident in sorted(rows, reverse=True)[:200]:
                    meta = json.loads(artifact(c, ident, 'campaign', 'campaign.json', 65536))
                    if meta.get('mode') in ('uart', 'ssh'):
                        result.append(dict(id=ident, mode=meta['mode'], modified_ns=modified))
                return self.reply(200, result)
            if url.path == '/api/details' and set(query) == {'campaign'}:
                return self.reply(200, details(c, query['campaign'][0]))
            if url.path == '/api/file' and set(query) == {'campaign', 'area', 'name'}:
                ident, area, name = (query[k][0] for k in ('campaign', 'area', 'name'))
                data = artifact(c, ident, area, name)
                return self.reply(200, data, 'application/octet-stream', ident + '-' + name)
            return self.reply(404, {'error': 'Route not found'})
        except FileNotFoundError:
            self.reply(404, {'error': 'Artifact not available'})
        except OverflowError as error:
            self.reply(413, {'error': str(error)})
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'Invalid request or metadata'})
        except OSError:
            self.reply(503, {'error': 'Artifact unavailable'})

    def do_POST(self):
        if not self.guard():
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 4096 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError('JSON body, 1..4096 bytes required')
            value = json.loads(self.rfile.read(length))
            if type(value) is not dict:
                raise ValueError('JSON object required')
            s = self.server.supervisor
            if self.path == '/api/mode' and set(value) == {'mode'}:
                s.select(value['mode'])
            elif self.path == '/api/start' and not value:
                s.start()
            elif self.path == '/api/resume' and set(value) == {'campaign'}:
                s.start(value['campaign'])
            elif self.path == '/api/stop' and not value:
                s.stop()
            else:
                raise ValueError('Invalid action or fields')
            self.reply(200, s.status())
        except (ValueError, TypeError, KeyError, OSError) as error:
            self.reply(400, {'error': str(error)})
        except RuntimeError as error:
            self.reply(409, {'error': str(error)})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, supervisor, token, port=8766):
        if type(token) is not str or len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError('Token must be >=32 ASCII characters')
        self.supervisor, self.token = supervisor, token
        super().__init__(('127.0.0.1', port), Handler)


def serve(config, port=8766):
    supervisor = Supervisor(config)
    token = os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server = Server(supervisor, token, port)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print('Panel http://127.0.0.1:%d / token: %s' % (server.server_port, token), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close(); supervisor.close()
