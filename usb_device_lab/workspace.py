"""Campaign-oriented APIs for the single-page operator workspace."""
import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .checkpoint import MAX_BYTES, canonical, parameters, validate
from .corpus_browser import CorpusBrowser
from .dashboard import DashboardHandler, DashboardServer
from .lab import load
from .log_tail import read_tail
from .web import ARTIFACTS
from .workspace_ui import PAGE, SCRIPT, STYLE

MAX_DOWNLOAD = 64 * 1024 * 1024


def checkpoint_info(root):
    try:
        fd = os.open(Path(root) / 'campaign-checkpoint.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                return None
            envelope = json.loads(os.pread(fd, MAX_BYTES + 1, 0))
        finally:
            os.close(fd)
        value = validate(envelope['checkpoint'])
        if envelope['sha256'] != hashlib.sha256(canonical(value)).hexdigest():
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def settings(config):
    return parameters({key: getattr(config, key) for key in ('iterations', 'seconds', 'seed', 'families', 'profiles')})


def connection_settings(config):
    return {key: str(value) for key, value in vars(config).items() if key not in settings(config)}


def readable_error(text):
    text = str(text or '')
    for keys, message in ((('checksum',), 'Сохранённое состояние повреждено.'),
                          (('digest', 'corpus'), 'Корпус изменился или содержит недоступные файлы.'),
                          (('gap', 'inconsistent', 'tracking'), 'Результаты не совпадают с сохранённым состоянием. Начните новую кампанию после проверки стенда.'),
                          (('completed',), 'Все попытки этой кампании уже выполнены.'),
                          (('missing',), 'Нет сохранённого состояния для продолжения.'),
                          (('checks failed',), 'Стенд не прошёл проверку готовности.'),
                          (('unfinished',), 'Есть незавершённая попытка. Проверьте её логи и начните новую кампанию.'),
                          (('mismatch',), 'Настройки или версия программы изменились. Продолжение запрещено.')):
        if any(key in text.lower() for key in keys):
            return message
    return 'Кампания остановлена из-за ошибки. Откройте результаты и логи.' if text else ''


class WorkspaceServer(DashboardServer):
    def __init__(self, supervisor, token, results_dir, port=8765, config=None):
        if config is None:
            raise ValueError('Workspace requires lab configuration')
        super().__init__(supervisor, token, results_dir, port, config=config)
        self.lab_config = config
        self.pending_previous = None
        self.RequestHandlerClass = WorkspaceHandler

    def query(self, sql, values=()):
        database = self.results_dir.resolve() / 'runs.sqlite3'
        if not database.exists():
            return []
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'campaign_attempts' not in tables:
                db.execute('CREATE TEMP TABLE campaign_attempts(campaign_id TEXT,iteration INTEGER,run_id TEXT)')
            return [dict(row) for row in db.execute(sql, values)]

    def campaigns(self):
        rows = self.query('SELECT a.campaign_id AS id,MIN(r.started) AS started,COUNT(*) AS attempts,'
                          'SUM(CASE WHEN r.status IN ("ok","error") THEN 1 ELSE 0 END) AS done '
                          'FROM campaign_attempts a JOIN runs r ON r.id=a.run_id GROUP BY a.campaign_id '
                          'ORDER BY started DESC LIMIT 30')
        legacy = self.query('SELECT MIN(r.started) AS started,COUNT(*) AS attempts,SUM(CASE WHEN r.status IN ("ok","error") THEN 1 ELSE 0 END) AS done FROM runs r '
                            'LEFT JOIN campaign_attempts a ON a.run_id=r.id WHERE a.run_id IS NULL')
        if legacy and legacy[0]['attempts']:
            rows.append(dict(legacy[0], id='legacy'))
        value = checkpoint_info(self.results_dir)
        if value and all(row['id'] != value['campaign_id'] for row in rows):
            rows.insert(0, {'id': value['campaign_id'], 'started': None, 'attempts': 0, 'done': value['next_iteration']})
        return rows

    def runs(self, campaign):
        if campaign != 'legacy' and re.fullmatch('[0-9a-f]{32}', campaign) is None:
            raise ValueError('Invalid campaign')
        clause, args = ('a.run_id IS NULL', ()) if campaign == 'legacy' else ('a.campaign_id=?', (campaign,))
        base = '''SELECT r.id,r.started,r.status,a.iteration,CASE WHEN json_valid(r.result) THEN
                  json_object('verdict',json_extract(r.result,'$.verdict'),'new_pcs',json_extract(r.result,'$.new_pcs'),
                  'errors',json_extract(r.result,'$.errors'),'kernel_events',json_extract(r.result,'$.kernel_events'))
                  ELSE NULL END AS result FROM runs r LEFT JOIN campaign_attempts a ON a.run_id=r.id WHERE '''
        def view(row):
            raw = row.pop('result', None)
            result = json.loads(raw) if raw and len(raw) <= 2*1024*1024 else {}
            if type(result) is not dict:
                result = {}
            verdict = result.get('verdict') or {}
            if type(verdict) is not dict:
                verdict = {}
            row.update(outcome=verdict.get('outcome', row['status'] if row['status'] in ('running','interrupted') else 'inconclusive'), confirmation=verdict.get('confirmation', 'unknown'),
                       new_pcs=result.get('new_pcs'), errors=result.get('errors', [])[:20], kernel_events=result.get('kernel_events', [])[:20])
            return row
        return {'rows': [view(row) for row in self.query(base + clause + ' ORDER BY r.started DESC LIMIT 100', args)],
                'errors': [view(row) for row in self.query(base + clause + ' AND r.status="error" ORDER BY r.started DESC LIMIT 100', args)]}

    def member(self, campaign, run):
        if re.fullmatch('[0-9a-f]{32}', run) is None:
            raise ValueError('Invalid run')
        clause, args = ('a.run_id IS NULL', (run,)) if campaign == 'legacy' else ('a.campaign_id=?', (run, campaign))
        if campaign != 'legacy' and re.fullmatch('[0-9a-f]{32}', campaign) is None:
            raise ValueError('Invalid campaign')
        if not self.query('SELECT r.id FROM runs r LEFT JOIN campaign_attempts a ON a.run_id=r.id WHERE r.id=? AND ' + clause, args):
            raise FileNotFoundError('Run does not belong to campaign')

    def overview(self):
        fresh = load(self.lab_config.path)
        with self.supervisor._lock:
            status = self.supervisor.status()
            active = settings(self.supervisor.config) if self.supervisor._last is None else parameters(self.supervisor._last)
        value = checkpoint_info(self.results_dir)
        current = value['campaign_id'] if value else None
        if self.pending_previous == current:
            current = None
        return {'status': status['state'], 'completed': status['runs'], 'active': active, 'configured': settings(fresh),
                'campaigns': self.campaigns(), 'current': current, 'failure': readable_error(status.get('error') or status.get('persistence_error')),
                'has_history': self.supervisor._last is not None,
                'restart_required': connection_settings(fresh) != connection_settings(self.lab_config)}


class WorkspaceHandler(DashboardHandler):
    def do_GET(self):
        if self.path in ('/', '/results', '/logs', '/corpus', '/events', '/workspace.js', '/workspace.css'):
            if self.guard(authenticated=False):
                asset = SCRIPT if self.path == '/workspace.js' else STYLE if self.path == '/workspace.css' else PAGE
                kind = 'text/javascript' if self.path.endswith('.js') else 'text/css' if self.path.endswith('.css') else 'text/html'
                self.reply(200, asset, kind + '; charset=utf-8')
            return
        url = urlsplit(self.path)
        if not url.path.startswith('/api/workspace/'):
            return super().do_GET()
        if not self.guard():
            return
        try:
            q = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
            if any(len(values) != 1 for values in q.values()):
                raise ValueError('Duplicate query')
            value = lambda key, default='': q.get(key, [default])[0]
            allowed = {'/api/workspace/status': set(), '/api/workspace/runs': {'campaign'},
                       '/api/workspace/tail': {'campaign', 'run', 'source'}, '/api/workspace/file': {'campaign', 'run', 'name'}}
            if url.path not in allowed or set(q)-allowed[url.path]:
                raise ValueError('Invalid query')
            if url.path.endswith('/status'):
                data = self.server.overview()
            elif url.path.endswith('/runs'):
                data = self.server.runs(value('campaign'))
            else:
                self.server.member(value('campaign'), value('run'))
                if url.path.endswith('/tail'):
                    data = read_tail(self.server.results_dir, value('run'), value('source'), 200)
                else:
                    return self.download(value('run'), value('name'))
            self.reply(200, data)
        except FileNotFoundError:
            self.reply(404, {'error': 'Файл пока не готов или попытка недоступна.'})
        except ValueError:
            self.reply(400, {'error': 'Не удалось прочитать настройки или запрос.'})
        except (OSError, sqlite3.Error, TypeError, KeyError, AttributeError):
            self.reply(503, {'error': 'Данные временно недоступны. Попробуйте обновить страницу.'})

    def do_POST(self):
        if self.path not in ('/api/workspace/start', '/api/workspace/stop', '/api/workspace/resume'):
            return super().do_POST()
        if not self.guard():
            return
        try:
            lengths = self.headers.get_all('Content-Length') or []
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or int(lengths[0])>1024:
                raise ValueError('Invalid body')
            if self.headers.get_content_type() != 'application/json' or json.loads(self.rfile.read(int(lengths[0]))) != {}:
                raise ValueError('Only empty command body allowed')
            with self.server.action_lock, self.server.supervisor._lock:
                if self.server.closing:
                    self.reply(503, {'error': 'Сайт завершает работу.'})
                    return
                action = self.path.rsplit('/', 1)[-1]
                if action == 'start':
                    fresh = load(self.server.lab_config.path)
                    if connection_settings(fresh) != connection_settings(self.server.lab_config):
                        raise RuntimeError('Настройки стенда изменились. Перезапустите сайт.')
                    if self.server.supervisor._thread is not None and self.server.supervisor._thread.is_alive():
                        raise RuntimeError('Кампания уже выполняется.')
                    old = checkpoint_info(self.server.results_dir)
                    self.server.pending_previous = old['campaign_id'] if old else None
                    self.server.lab_config = fresh
                    self.server.supervisor.config = fresh
                    self.server.corpus_browser = CorpusBrowser(fresh)
                    self.server.supervisor.start()
                elif action == 'resume':
                    fresh = load(self.server.lab_config.path)
                    last = self.server.supervisor._last
                    if last is None or settings(fresh) != parameters(last) or connection_settings(fresh) != connection_settings(self.server.lab_config):
                        raise RuntimeError('Настройки изменились или сохранённой кампании нет. Начните новую кампанию.')
                    self.server.pending_previous = None
                    self.server.supervisor.resume()
                else:
                    self.server.supervisor.stop()
            self.reply(202, self.server.overview())
        except ValueError:
            self.reply(400, {'error': 'Запрос или конфигурация некорректны. Настройки запуска берутся из lab.toml.'})
        except RuntimeError as error:
            self.reply(409, {'error': str(error) if not str(error).isascii() else readable_error(error)})
        except OSError:
            self.reply(503, {'error': 'Не удалось прочитать файл конфигурации.'})

    def download(self, run, name):
        if name not in ARTIFACTS:
            raise ValueError('Unsupported artifact')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(self.server.results_dir.resolve(), flags)
        fd = None
        try:
            for component in ('runs', run):
                child = os.open(component, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Not a regular artifact')
            if info.st_size > MAX_DOWNLOAD:
                self.reply(413, {'error': 'Файл больше 64 МиБ. Скопируйте его со стенда после остановки кампании.'})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(info.st_size))
            self.send_header('Content-Disposition', 'attachment; filename="' + run[:8] + '-' + name + '"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            remaining = info.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    return
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
