"""Loopback-only, read-only dashboard with explicit triage outcomes."""
import json
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ARTIFACTS = frozenset({
    'config.json', 'input.executed.json', 'metadata.json', 'run.json',
    'coverage.json', 'kernel_events.json', 'verdict.json', 'result.json',
    'kmsg.delta.log', 'agent.log', 'executor.log',
})
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def run_view(row):
    item = dict(row)
    result = json.loads(item['result']) if item.get('result') else None
    item['result'] = result
    verdict = (result or {}).get('verdict')
    if not isinstance(verdict, dict):
        verdict = {
            'outcome': item['status'] if item['status'] in ('running', 'interrupted') else 'legacy_unclassified',
            'confirmation': 'unknown',
            'kernel_evidence_detected': None,
            'telemetry_complete': None,
        }
    item['verdict'] = verdict
    item['new_pcs'] = (result or {}).get('new_pcs')
    return item


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USB Device Lab — Triage</title>
<style>
:root{color-scheme:dark;--bg:#0b1220;--panel:#121d30;--line:#26344a;--text:#e7edf7;--muted:#9cacbf;--accent:#7bb4ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:28px}header{display:flex;justify-content:space-between;gap:16px;align-items:center}h1{margin:0}h2{font-size:19px}p{color:var(--muted);line-height:1.5}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}button,select{background:#1c2b43;color:var(--text);border:1px solid var(--line);padding:9px 13px;border-radius:8px}button{cursor:pointer}button:hover{border-color:var(--accent)}a{color:var(--accent)}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-size:13px}.badge{display:inline-block;padding:5px 9px;border-radius:20px;background:#25354b;font-size:12px}.kernel_candidate{background:#5f2635;color:#ffd6df}.new_coverage{background:#153f35;color:#aaf0cb}.infrastructure_failure,.executor_failure,.unclassified_failure{background:#573b1c;color:#ffe1ad}.no_change{background:#25354b}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1423;border-radius:8px;padding:14px;max-height:500px;overflow:auto;font-size:12px}.links{display:flex;gap:14px;flex-wrap:wrap}.notice{border-left:3px solid #d5a253;padding-left:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}#error{color:#ffb2c1}small{color:var(--muted)}details{margin:12px 0}summary{cursor:pointer}@media(max-width:600px){main{padding:14px}header{align-items:flex-start;flex-direction:column}}
</style></head><body><main>
<header><div><h1>USB Device Lab</h1><p>Runs · Kernel evidence · Coverage</p></div><span class="badge">Read-only / localhost</span></header>
<p class="notice">Kernel candidate — обнаруженное сообщение ядра, а не подтверждённый баг. Replay и подтверждение ещё не реализованы. New coverage — не ошибка.</p>
<section class="card"><div class="toolbar"><label>Статус БД <select id="filter"><option value="">Все</option><option>error</option><option>ok</option><option>interrupted</option><option>running</option></select></label><button id="reload">Обновить</button><button id="prev">Назад</button><button id="next">Далее</button><label><input id="auto" type="checkbox" checked> Автообновление, 5 с</label><small id="page"></small></div><p id="error" role="alert"></p>
<div class="tablewrap"><table><thead><tr><th>Run</th><th>Итог triage</th><th>New PCs</th><th>Начало</th><th></th></tr></thead><tbody id="rows"></tbody></table></div></section>
<section class="card"><h2>Детали запуска</h2><p id="hint">Выберите запуск в таблице.</p><div id="metrics" class="grid"></div><p id="evidence-note"></p><div id="downloads" class="links"></div><h2>Kernel events</h2><div id="events"></div><h2>Диагностика executor / infrastructure</h2><pre id="diagnostics">—</pre><details><summary>Полный результат и metadata</summary><pre id="details">—</pre></details></section>
<section class="card"><h2>Группы диагностик</h2><p>Старый индекс errors: содержит и сообщения ядра, и ошибки стенда. Это не список подтверждённых багов.</p><pre id="errors">—</pre></section>
<script>
let offset=0,busy=false,selected=null,detailToken=0;
const el=id=>document.getElementById(id);
const names={new_coverage:'Новое покрытие',no_change:'Без нового покрытия',kernel_candidate:'Kernel candidate · не подтверждён',infrastructure_failure:'Ошибка инфраструктуры',executor_failure:'Ошибка исполнителя',unclassified_failure:'Неклассифицированная ошибка',inconclusive:'Недостаточно данных',legacy_unclassified:'Legacy · triage отсутствует',running:'Выполняется',interrupted:'Прерван'};
async function get(url){const r=await fetch(url);if(!r.ok)throw new Error(url+': HTTP '+r.status);return r.json()}
function badge(outcome){const b=document.createElement('span');b.className='badge';if(Object.hasOwn(names,outcome))b.classList.add(outcome);b.textContent=names[outcome]||outcome;return b}
function metric(label,value){const d=document.createElement('div'),s=document.createElement('small'),p=document.createElement('p');s.textContent=label;p.textContent=value;d.append(s,p);return d}
async function refresh(){if(busy)return;busy=true;try{const q=new URLSearchParams({offset,status:el('filter').value});const runs=await get('/api/runs?'+q);el('rows').replaceChildren();for(const x of runs){const tr=document.createElement('tr');let td=document.createElement('td');td.textContent=x.id.slice(0,12);tr.append(td);td=document.createElement('td');td.append(badge(x.verdict.outcome));tr.append(td);for(const v of [x.new_pcs??'—',new Date(x.started*1000).toLocaleString()]){td=document.createElement('td');td.textContent=v;tr.append(td)}td=document.createElement('td');const b=document.createElement('button');b.textContent='Открыть';b.onclick=()=>openRun(x.id).catch(showError);td.append(b);tr.append(td);el('rows').append(tr)}el('page').textContent='Записи '+(offset+1)+'–'+(offset+runs.length);el('errors').textContent=JSON.stringify(await get('/api/errors'),null,2);el('error').textContent='';}catch(e){showError(e)}finally{busy=false}}
function showError(e){el('error').textContent=String(e.message||e)}
async function openRun(id){selected=id;const token=++detailToken;const x=await get('/api/run/'+id);if(token!==detailToken)return;const v=x.verdict,r=x.result||{};el('hint').textContent='Run '+id;el('metrics').replaceChildren(metric('Итог',names[v.outcome]||v.outcome),metric('Подтверждение',v.confirmation||'unknown'),metric('New PCs',x.new_pcs??'—'),metric('Телеметрия',v.telemetry_complete===true?'Полная по текущим признакам':v.telemetry_complete===false?'Неполная':'Неизвестно'));el('evidence-note').textContent=v.kernel_evidence_detected?'Есть kernel evidence. Нужны анализ и воспроизведение.':'Отсутствие отчёта не доказывает отсутствие бага ядра.';el('downloads').replaceChildren();for(const name of x.artifacts){const a=document.createElement('a');a.href='/api/artifact/'+id+'/'+encodeURIComponent(name);a.textContent=name;a.download=name;el('downloads').append(a)}el('events').replaceChildren();for(const event of r.kernel_events||[]){const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');s.textContent=event.kind+': '+event.summary;p.textContent=event.context||event.raw_summary;d.append(s,p);el('events').append(d)}if(!(r.kernel_events||[]).length)el('events').textContent='Сохранённых kernel events нет.';el('diagnostics').textContent=JSON.stringify((r.errors||[]).filter(e=>e.kind!=='kernel'),null,2);el('details').textContent=JSON.stringify(x,null,2)}
el('reload').onclick=()=>{refresh();if(selected)openRun(selected).catch(showError)};el('filter').onchange=()=>{offset=0;refresh()};el('prev').onclick=()=>{offset=Math.max(0,offset-50);refresh()};el('next').onclick=()=>{offset+=50;refresh()};setInterval(()=>{if(el('auto').checked)refresh()},5000);refresh();
</script></main></body></html>"""


def make_server(directory, port=8080):
    root = Path(directory).resolve()
    database = root / 'runs.sqlite3'
    if not database.exists():
        raise ValueError('campaign database not found')

    class Handler(BaseHTTPRequestHandler):
        def allowed_host(self):
            host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]')
            return host in ('127.0.0.1', 'localhost', '::1')

        def artifact(self, ident, name):
            if name not in ARTIFACTS:
                return None
            path = (root / 'runs' / ident / name).resolve()
            if not path.is_relative_to(root / 'runs' / ident) or not path.is_file():
                return None
            return path

        def do_GET(self):
            if not self.allowed_host():
                return self.send_error(403)
            url = urlsplit(self.path)
            try:
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    if url.path == '/':
                        return self.reply(PAGE.encode(), 'text/html; charset=utf-8')
                    if url.path == '/api/runs':
                        q = parse_qs(url.query)
                        offset = max(0, min(int(q.get('offset', ['0'])[0]), 10000000))
                        status = q.get('status', [''])[0]
                        rows = db.execute('SELECT id,status,started,digest,result FROM runs WHERE (?="" OR status=?) ORDER BY started DESC LIMIT 50 OFFSET ?', (status, status, offset))
                        items = []
                        for row in rows:
                            item = run_view(row)
                            item.pop('result', None)
                            items.append(item)
                        return self.data(items)
                    if url.path == '/api/errors':
                        return self.data([dict(row) for row in db.execute('SELECT * FROM errors ORDER BY occurrences DESC LIMIT 200')])
                    match = re.fullmatch(r'/api/(run|config|artifact)/([0-9a-f]{32})(?:/([a-z.]+))?', url.path)
                    if match:
                        kind, ident, name = match.groups()
                        row = db.execute('SELECT * FROM runs WHERE id=?', (ident,)).fetchone()
                        if row is not None:
                            if kind in ('config', 'artifact'):
                                if kind == 'config' and name is not None:
                                    return self.send_error(404)
                                path = self.artifact(ident, 'config.json' if kind == 'config' else name)
                                if path is None:
                                    return self.send_error(404)
                                if path.stat().st_size > MAX_ARTIFACT_BYTES:
                                    return self.send_error(413)
                                content_type = 'application/json' if path.suffix == '.json' else 'text/plain; charset=utf-8'
                                return self.reply(path.read_bytes(), content_type)
                            if name is not None:
                                return self.send_error(404)
                            item = run_view(row)
                            meta = self.artifact(ident, 'metadata.json')
                            item['metadata'] = json.loads(meta.read_text()) if meta else None
                            if item['result'] is None:
                                verdict = self.artifact(ident, 'verdict.json')
                                if verdict:
                                    item['verdict'] = json.loads(verdict.read_text())
                            item['artifacts'] = [name for name in sorted(ARTIFACTS) if self.artifact(ident, name)]
                            return self.data(item)
                self.send_error(404)
            except (ValueError, OSError, sqlite3.Error, TypeError):
                self.send_error(400)

        def data(self, value):
            self.reply(json.dumps(value).encode(), 'application/json')

        def reply(self, body, content_type):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', CSP)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(directory, port=8080):
    server = make_server(directory, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='USB Device Lab read-only dashboard')
    parser.add_argument('--output', default='state')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    serve(args.output, args.port)
