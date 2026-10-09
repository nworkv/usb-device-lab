"""Authenticated log list/tail routes mixed into the unified dashboard."""
import errno
import re
from urllib.parse import parse_qs, urlsplit
from .log_tail import list_logs, read_tail

LOG_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>USB Device Lab — логи</title><link rel="stylesheet" href="/control.css"></head><body><main class="shell">
<nav class="actions"><a href="/">Управление</a><a href="/events">События</a><a href="/results">Результаты</a><a href="/logs">Логи</a></nav>
<header><h1>Логи запуска</h1><p class="subtitle">Только ограниченный хвост — не весь файл.</p></header>
<section class="glass panel"><label for="log-token">Токен доступа</label><input id="log-token" type="password" autocomplete="off">
<label for="log-run">Идентификатор запуска</label><input id="log-run" spellcheck="false" maxlength="32" placeholder="32 символа: 0–9, a–f">
<div class="actions"><button id="log-list">Показать список логов</button><button id="log-refresh">Обновить хвост</button></div>
<label for="log-source">Источник</label><select id="log-source"><option value="executor">Исполнитель</option><option value="agent">Host agent</option><option value="kernel">Сохранённый kernel log</option></select>
<label for="log-lines">Последних строк</label><input id="log-lines" type="number" min="1" max="1000" value="200">
<label class="toggle"><input id="log-live" type="checkbox"> Обновлять раз в 2 секунды (снимите флажок для паузы)</label>
<p class="hint">Идентификатор можно скопировать в разделе «Результаты». Максимум чтения — 64 КиБ. Kernel log доступен после сохранения агентом; это не прямая трансляция с хоста.</p>
<p id="log-notice" role="status" aria-live="polite">Введите токен и идентификатор запуска.</p><pre id="log-files"></pre><pre id="log-text" tabindex="0"></pre>
</section></main><script src="/logs.js"></script></body></html>
""".encode('utf-8')
LOG_SCRIPT = r"""'use strict';
const le=id=>document.getElementById(id);let logBusy=false, logGeneration=0;
for(const id of ['log-token','log-run','log-source','log-lines'])le(id).addEventListener('input',()=>{logGeneration++;le('log-text').textContent='';});
async function loadLog(list=false){
  if(logBusy)return;const token=le('log-token').value.trim(),run=le('log-run').value.trim(),lines=Number(le('log-lines').value);
  if(!token||!/^[0-9a-f]{32}$/.test(run)||!Number.isInteger(lines)||lines<1||lines>1000){le('log-notice').textContent='Проверьте токен, ID запуска и число строк (1–1000).';return;}
  const generation=logGeneration;logBusy=true;
  try{const url='/api/logs/'+run+(list?'':'/'+le('log-source').value+'?lines='+lines);const response=await fetch(url,{headers:{Authorization:'Bearer '+token}});const data=await response.json();if(generation!==logGeneration)return;
    if(!response.ok)throw new Error(response.status===401?'Токен не принят.':response.status===404?'Лог ещё не создан или недоступен.':'Ошибка чтения: HTTP '+response.status);
    if(list){le('log-files').textContent=data.map(x=>x.name+': '+(x.available?'доступен, '+x.size+' байт':'пока недоступен')).join('\n');le('log-notice').textContent='Список источников обновлён.';}
    else{le('log-text').textContent=data.text;le('log-notice').textContent='Прочитано '+data.bytes_read+' байт, строк '+data.lines_returned+(data.truncated?' · показан ограниченный хвост':'')+(data.changed_during_read?' · файл изменился при чтении':'')+(data.saved_kernel_log?' · сохранённый kernel log':'');}
  }catch(error){if(generation===logGeneration)le('log-notice').textContent=String(error.message||error);}finally{logBusy=false;}
}
le('log-list').addEventListener('click',()=>loadLog(true));le('log-refresh').addEventListener('click',()=>loadLog(false));setInterval(()=>{if(le('log-live').checked)loadLog(false);},2000);
""".encode('utf-8')


class LogRoutes:
    def do_GET(self):
        if self.path in ('/logs', '/logs.js'):
            if self.guard(authenticated=False):
                self.reply(200, LOG_PAGE if self.path == '/logs' else LOG_SCRIPT,
                           'text/html; charset=utf-8' if self.path == '/logs' else 'text/javascript; charset=utf-8')
            return
        parsed = urlsplit(self.path)
        if not parsed.path.startswith('/api/logs/'):
            return super().do_GET()
        if not self.guard():
            return
        match = re.fullmatch(r'/api/logs/([0-9a-f]{32})(?:/(executor|agent|kernel))?', parsed.path)
        if match is None:
            self.reply(400, {'error': 'Invalid run or source'})
            return
        run, source = match.groups()
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            if source is None:
                if query:
                    raise ValueError('Unexpected list parameters')
                data = list_logs(self.server.results_dir, run)
            else:
                if set(query) - {'lines'} or any(len(values) != 1 for values in query.values()):
                    raise ValueError('Invalid tail parameters')
                lines = query.get('lines', ['200'])[0]
                if not lines.isascii() or not lines.isdigit():
                    raise ValueError('Invalid lines')
                data = read_tail(self.server.results_dir, run, source, int(lines))
        except ValueError:
            self.reply(400, {'error': 'Invalid log request or file type'})
            return
        except OSError as error:
            status = 404 if error.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR, errno.EACCES) else 503
            self.reply(status, {'error': 'Log unavailable'})
            return
        self.reply(200, data)
