"""Unified local entry point: lab commands and a persistent event/results panel."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .event_control import EventControlHandler, EventControlServer, EventCampaignSupervisor, LIVE_SCRIPT
from .panel_ui import PAGE, STYLE
from .web import run_view
from .log_panel import LogRoutes
from .corpus_browser import CorpusBrowser
from .corpus_panel import CorpusRoutes

NAV = '<nav class="actions" aria-label="Разделы"><a href="/">Управление</a><a href="/events">События</a><a href="/results">Результаты</a><a href="/logs">Логи</a><a href="/corpus">Корпус</a></nav>'.encode('utf-8')
CONTROL_PAGE = PAGE.replace(b'<header>', NAV + b'<header>', 1)
DASH_STYLE = STYLE + b'nav.actions{margin-bottom:24px}nav a{color:var(--accent);padding:8px 12px;border:1px solid var(--border);border-radius:10px;text-decoration:none}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.table-wrap{overflow:auto}'
DASH_SCRIPT = LIVE_SCRIPT.replace(b'Live events (1s polling)', 'События: обновление раз в секунду'.encode('utf-8')) + b"\nif(location.pathname === '/events') live.checked = true;\n"
RESULTS_PAGE = ("""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USB Device Lab — результаты</title><link rel="stylesheet" href="/control.css"></head><body><main class="shell">
""".encode('utf-8') + NAV + """<header><h1>Результаты кампаний</h1><p class="subtitle">Сохранённые попытки и итог triage. Ошибка выполнения — не подтверждённый баг ядра.</p></header>
<section class="glass panel"><label for="results-token">Токен доступа</label><input id="results-token" type="password" autocomplete="off">
<p class="hint">При переходе между страницами введите токен снова: он не хранится в браузере.</p>
<div class="actions"><button id="results-load">Обновить</button><button id="results-prev">Назад</button><button id="results-next">Далее</button></div>
<p id="results-notice" role="status" aria-live="polite">Введите токен для просмотра результатов.</p>
<div class="table-wrap"><table><thead><tr><th>Попытка</th><th>Итог</th><th>Подтверждение</th><th>Новые PC</th><th>Начало</th></tr></thead><tbody id="results-rows"></tbody></table></div>
<p class="hint">Здесь показана сводка до 50 попыток на страницу. Детальные артефакты пока доступны через прежний read-only просмотрщик.</p>
</section></main><script src="/results.js"></script></body></html>
""".encode('utf-8'))
RESULTS_SCRIPT = """'use strict';
const re = id => document.getElementById(id);
let resultsOffset=0, resultsBusy=false;
const outcomes={kernel_candidate:'Кандидат: нужны анализ и воспроизведение',new_coverage:'Новое покрытие',no_change:'Без изменений',infrastructure_failure:'Ошибка стенда',executor_failure:'Ошибка исполнителя',inconclusive:'Недостаточно данных',legacy_unclassified:'Старый запуск без triage',running:'Выполняется',interrupted:'Прерван',unclassified_failure:'Неклассифицированная ошибка'};
async function loadResults(){
  if(resultsBusy)return;
  const token=re('results-token').value.trim();if(!token){re('results-notice').textContent='Введите токен.';return;}
  resultsBusy=true;
  try{
    const response=await fetch('/api/results?offset='+resultsOffset,{headers:{Authorization:'Bearer '+token}});
    if(!response.ok)throw new Error(response.status===401?'Токен не принят.':'Не удалось получить результаты: HTTP '+response.status);
    const rows=await response.json();re('results-rows').replaceChildren();
    for(const row of rows){const tr=document.createElement('tr');for(const value of [row.id,outcomes[row.outcome]||row.outcome,row.confirmation,row.new_pcs??'—',new Date(row.started*1000).toLocaleString('ru-RU')]){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}re('results-rows').append(tr);}
    re('results-notice').textContent=rows.length?'Показано '+rows.length+' попыток, смещение '+resultsOffset+'.':'На этой странице результатов нет.';
    re('results-prev').disabled=resultsOffset===0;re('results-next').disabled=rows.length<50;
  }catch(error){re('results-notice').textContent=String(error.message||error);}finally{resultsBusy=false;}
}
re('results-load').addEventListener('click',loadResults);
re('results-prev').addEventListener('click',()=>{if(!resultsBusy){resultsOffset=Math.max(0,resultsOffset-50);loadResults();}});
re('results-next').addEventListener('click',()=>{if(!resultsBusy){resultsOffset+=50;loadResults();}});
""".encode('utf-8')


def result_summaries(directory, offset=0):
    if type(offset) is not int or not 0 <= offset <= 10000000:
        raise ValueError('Invalid results offset')
    database = Path(directory).resolve() / 'runs.sqlite3'
    if not database.exists():
        return []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT id,status,started,digest,result FROM runs ORDER BY started DESC LIMIT 50 OFFSET ?', (offset,))
        summaries = []
        for row in rows:
            limited = bool(row['result'] and len(row['result'].encode('utf-8')) > 2 * 1024 * 1024)
            item = run_view(dict(row, result=None) if limited else row)
            verdict = item['verdict']
            summaries.append({'id': item['id'], 'status': item['status'], 'started': item['started'],
                              'outcome': 'inconclusive' if limited else str(verdict.get('outcome', 'inconclusive'))[:128],
                              'confirmation': str(verdict.get('confirmation', 'unknown'))[:128],
                              'new_pcs': item['new_pcs'] if type(item['new_pcs']) is int and 0 <= item['new_pcs'] < 2**63 else None, 'summary_limited': limited})
        return summaries


class DashboardHandler(CorpusRoutes, LogRoutes, EventControlHandler):
    def do_GET(self):
        assets = {'/': (CONTROL_PAGE, 'text/html'), '/events': (CONTROL_PAGE, 'text/html'),
                  '/results': (RESULTS_PAGE, 'text/html'), '/control.css': (DASH_STYLE, 'text/css'),
                  '/control.js': (DASH_SCRIPT, 'text/javascript'), '/results.js': (RESULTS_SCRIPT, 'text/javascript')}
        if self.path in assets:
            if self.guard(authenticated=False):
                body, kind = assets[self.path]
                self.reply(200, body, kind + '; charset=utf-8')
            return
        parsed = urlsplit(self.path)
        if parsed.path != '/api/results':
            return super().do_GET()
        if not self.guard():
            return
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            if set(query) - {'offset'} or any(len(values) != 1 for values in query.values()):
                raise ValueError('Invalid query')
            offset = query.get('offset', ['0'])[0]
            if not offset.isascii() or not offset.isdigit():
                raise ValueError('Numeric offset required')
            rows = result_summaries(self.server.results_dir, int(offset))
        except ValueError:
            self.reply(400, {'error': 'Invalid results request or stored result'})
            return
        except (OSError, sqlite3.Error, TypeError, AttributeError):
            self.reply(503, {'error': 'Results database unavailable'})
            return
        self.reply(200, rows)


class DashboardServer(EventControlServer):
    def __init__(self, supervisor, token, results_dir, port=8765, config=None):
        super().__init__(supervisor, token, port)
        self.results_dir = Path(results_dir)
        self.corpus_browser = CorpusBrowser(config) if config is not None else None
        self.RequestHandlerClass = DashboardHandler


def serve_dashboard(config, port=8765):
    import os
    import secrets
    import signal
    from .workspace import WorkspaceServer
    token = os.environ.get('USB_DEVICE_LAB_CONTROL_TOKEN') or secrets.token_urlsafe(32)
    with EventCampaignSupervisor(config) as supervisor:
        server = WorkspaceServer(supervisor, token, config.results_dir, port, config=config)
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        print('Панель: ' + server.expected_origin, flush=True)
        print('Локальный токен: ' + token, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            with server.action_lock:
                server.closing = True
                supervisor.stop()
            server.server_close()
            supervisor.close()
            signal.signal(signal.SIGTERM, previous)
    return 0


def main(argv=None, serve=serve_dashboard, lab_main=None):
    import argparse
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('init', 'check', 'fuzz', 'replay'):
        if lab_main is None:
            from .lab import main as lab_main
        return lab_main(argv)
    parser = argparse.ArgumentParser(description='USB Device Lab: единая локальная точка запуска')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'check', 'fuzz', 'replay'):
        sub.add_parser(command, help='Совместимая команда lab; параметры смотрите через ' + command + ' --help')
    panel = sub.add_parser('serve', help='Управление, события и сводка результатов на одном порту')
    panel.add_argument('--config', default='lab.toml')
    panel.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    from .lab import load
    return serve(load(args.config), args.port)


if __name__ == '__main__':
    raise SystemExit(main())
