"""Authenticated campaign controls mounted on the event research console."""
import json
import os
import secrets
import signal
import threading
from urllib.parse import urlsplit
from .event_console import Supervisor as EventSupervisor, Handler as EventHandler, Server as EventServer
from .event_console import PAGE, PANEL
from .research_ui import SCRIPT as RESEARCH_SCRIPT
from .dual_pipeline import run_complete
from .device_cards import SCRIPT as DEVICE_SCRIPT
from .campaign_options import SCRIPT as OPTIONS_SCRIPT, validate_options, option_values, apply_options, window_budget

SKIP_SCRIPT = """'use strict';
el('skip-attempt').onclick=()=>{el('skip-attempt').disabled=true;action('/api/skip',{});};
setInterval(()=>{const b=el('skip-attempt');b.disabled=!token||!state?.skip_allowed||Boolean(state?.skip_pending);b.textContent=state?.skip_pending?'Пропуск запрошен':'Пропустить одну попытку';},250);
""".encode('utf-8') + DEVICE_SCRIPT + OPTIONS_SCRIPT


class Supervisor(EventSupervisor):
    def __init__(self, config):
        super().__init__(config)
        self.skip_event = threading.Event()
        self.options = {}

    def start(self, ident=None):
        with self.lock:
            if self.closing or self.busy():
                raise RuntimeError('Запуск или анализ уже выполняется')
            self.skip_event = threading.Event()
            return super().start(ident)

    def set_options(self, raw):
        with self.lock:
            if self.closing or self.busy():
                raise RuntimeError('Настройки запрещено менять во время запуска или анализа')
            self.options = validate_options(raw)

    def skip_allowed(self):
        return (not self.closing and self.busy() and self.stage == 'running'
            and self.active is not None and not self.stop_event.is_set() and not self.replay_source)

    def skip_current(self):
        with self.lock:
            if not self.skip_allowed():
                raise RuntimeError('Пропуск доступен только во время обычной кампании, не анализа или повтора')
            if self.skip_event.is_set():
                raise RuntimeError('Запрос пропуска уже ожидает обработки')
            self.skip_event.set()

    def status(self):
        with self.lock:
            value = super().status()
            options = option_values(self.config, getattr(self, 'options', {}))
            value.update(skip_allowed=self.skip_allowed(), skip_pending=self.skip_event.is_set(),
                campaign_options=options, estimated_window_seconds=window_budget(options))
            if not self.replay_source:
                value.update(iterations=options['iterations'], seconds=options['seconds'])
            return value

    def work(self, resume):
        def capture(receiver, directory):
            with self.lock:
                self.receiver, self.active = receiver, directory.name
        def stage(value):
            with self.lock:
                self.stage = value
        try:
            with self.lock:
                config = apply_options(self.config, getattr(self, 'options', {}))
            result = run_complete(config, resume=resume, stop=self.stop_event,
                skip=self.skip_event, on_capture=capture, on_stage=stage)
            with self.lock:
                self.result = result
                self.error = result.get('execution_error') or result.get('analysis_error')
                self.stage = 'failed' if self.error else 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            with self.lock:
                self.error, self.stage = str(error), 'failed'


class Handler(EventHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path not in ('/', '/skip.js', '/research.js'):
            return super().do_GET()
        if not self.guard(auth=False):
            return
        if self.path != url.path:
            return self.reply(400, {'error': 'Параметры страницы не допускаются'})
        if url.path == '/skip.js':
            return self.reply(200, SKIP_SCRIPT, 'text/javascript; charset=utf-8')
        if url.path == '/research.js':
            marker = "r.interrupted?'Прервана':".encode('utf-8')
            if RESEARCH_SCRIPT.count(marker) != 1:
                raise RuntimeError('Версия скрипта панели изменилась')
            script = RESEARCH_SCRIPT.replace(marker, "r.skipped?'Пропущена пользователем':r.interrupted?'Прервана':".encode('utf-8'), 1)
            return self.reply(200, script, 'text/javascript; charset=utf-8')
        marker = b'<section id="results" class="page" hidden>'
        scripts = b'<script src="/research.js"></script>'
        stop = b'<button id="stop" class="danger">'
        if PAGE.count(marker) != 1 or PAGE.count(scripts) != 1 or PAGE.count(stop) != 1:
            raise RuntimeError('Версия основной страницы изменилась')
        button = '<button id="skip-attempt" disabled>Пропустить одну попытку</button>'.encode('utf-8')
        page = PAGE.replace(marker, marker + PANEL, 1).replace(stop, button + stop, 1)
        page = page.replace(scripts, scripts + b'<script src="/events.js"></script><script src="/skip.js"></script>', 1)
        return self.reply(200, page, 'text/html; charset=utf-8')

    def do_POST(self):
        if self.path not in ('/api/skip', '/api/options'):
            return super().do_POST()
        if not self.guard():
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 4096 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError('Требуется ограниченный JSON')
            value = json.loads(self.rfile.read(size))
            if self.path == '/api/options':
                self.server.supervisor.set_options(value)
            else:
                if type(value) is not dict or value:
                    raise ValueError('Ожидается пустой JSON-объект')
                self.server.supervisor.skip_current()
            self.reply(200, self.server.supervisor.status())
        except RuntimeError as error:
            self.reply(409, {'error': str(error)})
        except (ValueError, TypeError, OSError) as error:
            self.reply(400, {'error': str(error)})


class Server(EventServer):
    def __init__(self, supervisor, token, port=8766):
        super().__init__(supervisor, token, port)
        self.RequestHandlerClass = Handler


def serve(config, port=8766):
    supervisor = Supervisor(config)
    token = os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server = Server(supervisor, token, port)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s' % (server.server_port, token), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        supervisor.close()
        server.server_close()
