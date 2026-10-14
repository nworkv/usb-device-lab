"""Separate loopback-only UART control page. Existing SSH workspace is unchanged."""
import argparse
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

UUID = re.compile(r'[0-9a-f]{32}')
LIMIT = 32 * 1024 * 1024


def artifact(config, campaign, area, name):
    if UUID.fullmatch(campaign) is None:
        raise ValueError('Invalid campaign')
    parts = [campaign]
    root = config.results_dir
    if area == 'capture':
        root = config.logs_dir / 'uart'
        allowed = {'uart.raw', 'chunks.jsonl', 'events.jsonl'}
    elif area == 'campaign':
        allowed = {'campaign.json', 'checkpoint.json'}
    elif re.fullmatch(r'attempt-[0-9]{6}', area):
        parts.append(area)
        allowed = {'config.json', 'started.json', 'window.json', 'result.json', 'executor.log'}
    elif re.fullmatch(r'evidence-[0-9a-f]{32}', area):
        parts.append(area)
        allowed = {'summary.json', 'analysis.json', 'complete.json'}
        if re.fullmatch(r'report-[0-9]{6}\.json', name):
            allowed.add(name)
    else:
        raise ValueError('Invalid artifact area')
    if name not in allowed:
        raise ValueError('Artifact is not allowed')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Not a regular artifact')
            if info.st_size > LIMIT:
                raise OverflowError('Artifact exceeds 32 MiB')
            data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise OverflowError('Artifact grew beyond 32 MiB')
            return data
    finally:
        os.close(directory)


class Supervisor:
    def __init__(self, config, seeds):
        self.config, self.seeds = config, tuple(Path(p).resolve() for p in seeds)
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        self.thread, self.receiver, self.result, self.error = None, None, None, None
        self.state, self.closing = 'idle', False

    def status(self):
        with self.lock:
            return dict(state=self.state, error=self.error, campaign=self.result,
                uart=self.receiver.status() if self.receiver is not None else None,
                configured=dict(iterations=self.config.iterations, seconds=self.config.seconds,
                                seed=self.config.seed, port=self.config.uart.port))

    def start(self, campaign=None):
        from .uart_config import load
        from .uart_campaign import run_campaign
        from .uart_receiver import UARTReceiver
        with self.lock:
            if self.closing or self.thread is not None and self.thread.is_alive():
                raise RuntimeError('Campaign is active or server is closing')
            if load(self.config.path) != self.config:
                raise RuntimeError('Configuration changed; restart the UART web process')
            resume = None
            if campaign is not None:
                artifact(self.config, campaign, 'campaign', 'checkpoint.json')
                resume = self.config.results_dir / campaign / 'checkpoint.json'
            self.stop_event = threading.Event()
            self.result, self.error, self.receiver, self.state = None, None, None, 'starting'
            def receiver_factory(settings, directory):
                receiver = UARTReceiver(settings, directory)
                with self.lock:
                    self.receiver = receiver
                    if self.state != 'stopping':
                        self.state = 'running'
                return receiver
            def work():
                try:
                    result = run_campaign(self.config, list(self.seeds), stop=self.stop_event,
                        receiver_factory=receiver_factory, resume=resume)
                    with self.lock:
                        self.result = dict(campaign_id=result['campaign_id'], directory=result['directory'],
                            attempts=len(result['outcomes']))
                        self.state = 'stopped' if self.stop_event.is_set() else 'completed'
                except Exception as error:
                    with self.lock:
                        self.error, self.state = str(error), 'failed'
            self.thread = threading.Thread(target=work, name='uart-web-campaign', daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            if self.thread is not None and self.thread.is_alive():
                self.state = 'stopping'

    def close(self):
        with self.lock:
            self.closing = True
            self.stop()
            thread = self.thread
        if thread is not None:
            thread.join(30)
            if thread.is_alive():
                raise RuntimeError('Campaign worker did not stop; inspect stand/processes')


def campaigns(config):
    if not config.results_dir.exists():
        return []
    root = os.open(config.results_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        names = [name for name in os.listdir(root) if UUID.fullmatch(name)]
        if len(names) > 1000:
            raise OverflowError('More than 1000 campaigns; archive old directories')
        rows = []
        for name in sorted(names):
            try:
                metadata = json.loads(artifact(config, name, 'campaign', 'campaign.json'))
                if metadata.get('capture_id') != name or metadata.get('mode') != 'uart':
                    continue
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                try:
                    children = os.listdir(directory)
                finally:
                    os.close(directory)
                rows.append(dict(id=name,
                    attempts=sorted(x for x in children if re.fullmatch(r'attempt-[0-9]{6}', x)),
                    evidence=sorted(x for x in children if re.fullmatch(r'evidence-[0-9a-f]{32}', x)),
                    checkpoint='checkpoint.json' in children, resumed_from=metadata.get('resumed_from')))
            except (OSError, ValueError, AttributeError):
                continue
        return rows
    finally:
        os.close(root)


PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>UART USB Lab</title>
<h1>UART: управление кампанией</h1><p>Отдельная панель без SSH/KCOV. Проверяйте стенд перед запуском.</p>
<input id="token" type="password" autocomplete="off" placeholder="Токен доступа"><button id="login">Войти</button><button id="logout">Выйти</button>
<p id="error"></p><pre id="state"></pre><button id="start">Новая кампания</button><button id="stop">Остановить</button>
<select id="campaign"></select><button id="resume">Продолжить выбранную</button><button id="files">Файлы выбранной кампании</button>
<div id="downloads"></div><pre id="report"></pre><script src="/uart.js"></script></html>""".encode("utf-8")
SCRIPT = b"""'use strict';
let token='',busy=false,epoch=0,abort=new AbortController();const el=id=>document.getElementById(id);
async function api(path,body){const generation=epoch;const options={signal:abort.signal,headers:{Authorization:'Bearer '+token}};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}const response=await fetch(path,options);const data=await response.json();if(generation!==epoch)throw new Error('Stale request');if(!response.ok)throw new Error(data.error||'Request failed');return data;}
async function refresh(){if(!token||busy)return;busy=true;const generation=epoch;try{const state=await api('/api/state');el('state').textContent=JSON.stringify(state,null,2);const rows=await api('/api/campaigns');const selected=el('campaign').value;el('campaign').replaceChildren();for(const row of rows){const item=document.createElement('option');item.value=row.id;item.textContent=row.id+' / attempts: '+row.attempts.length;el('campaign').append(item);}el('campaign').value=selected||rows.at(-1)?.id||'';window.rows=rows;}catch(error){if(generation===epoch)el('error').textContent=error.message;}finally{if(generation===epoch)busy=false;}}
async function command(action,body){if(!token)return;if(action!=='stop'&&!confirm('This starts USB activity on the stand. Continue?'))return;try{await api('/api/'+action,body);await refresh();}catch(error){el('error').textContent=error.message;}}
el('login').onclick=()=>{token=el('token').value;el('token').value='';refresh();};el('logout').onclick=()=>{epoch++;abort.abort();abort=new AbortController();busy=false;token='';el('state').textContent='';el('report').textContent='';el('error').textContent='';el('downloads').replaceChildren();el('campaign').replaceChildren();window.rows=[];};el('start').onclick=()=>command('start',{});el('stop').onclick=()=>command('stop',{});el('resume').onclick=()=>command('resume',{campaign:el('campaign').value});
el('files').onclick=()=>{el('downloads').replaceChildren();const row=(window.rows||[]).find(x=>x.id===el('campaign').value);if(!row)return;const entries=[['campaign','campaign.json'],['campaign','checkpoint.json'],['capture','uart.raw'],['capture','chunks.jsonl'],['capture','events.jsonl']];for(const a of row.attempts)for(const n of ['config.json','started.json','window.json','result.json','executor.log'])entries.push([a,n]);for(const e of row.evidence)for(const n of ['summary.json','analysis.json','complete.json'])entries.push([e,n]);for(const [area,name] of entries){const button=document.createElement('button');button.textContent=area+'/'+name;button.onclick=async()=>{const generation=epoch;try{const q=new URLSearchParams({campaign:row.id,area,name});const response=await fetch('/api/file?'+q,{signal:abort.signal,headers:{Authorization:'Bearer '+token}});if(!response.ok)throw new Error((await response.json()).error);const blob=await response.blob();if(generation!==epoch)return;if(name.endsWith('.json')){const text=await blob.text();if(generation!==epoch)return;el('report').textContent=text;}const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=row.id+'-'+name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){el('error').textContent=error.message;}};el('downloads').append(button);}};setInterval(refresh,3000);
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, kind='application/json', name=None):
        if type(data) is not bytes:
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        for key, value in [('Content-Type', kind), ('Content-Length', str(len(data))),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")]:
            self.send_header(key, value)
        if name:
            self.send_header('Content-Disposition', 'attachment; filename="' + name + '"')
        self.end_headers()
        self.wfile.write(data)

    def guard(self, auth=True):
        hosts = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if len(self.headers.get_all('Host') or []) != 1 or len(self.headers.get_all('Origin') or []) > 1 or auth and len(self.headers.get_all('Authorization') or []) != 1:
            self.reply(403, {'error':'Duplicate/missing security header'})
            return False
        host = self.headers.get('Host')
        if host not in hosts or self.headers.get('Origin') not in (None, 'http://' + host):
            self.reply(403, {'error':'Wrong Host/Origin'})
            return False
        if auth and not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + self.server.token).encode('ascii')):
            self.reply(401, {'error':'Authentication required'})
            return False
        return True

    def do_GET(self):
        if not self.guard(auth=self.path not in ('/', '/uart.js')):
            return
        try:
            url = urlsplit(self.path)
            q = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
            if any(len(v) != 1 for v in q.values()):
                raise ValueError('Duplicate query')
            if self.path == '/':
                return self.reply(200, PAGE, 'text/html; charset=utf-8')
            if self.path == '/uart.js':
                return self.reply(200, SCRIPT, 'text/javascript; charset=utf-8')
            if url.path == '/api/state' and not q:
                data = self.server.supervisor.status()
            elif url.path == '/api/campaigns' and not q:
                data = campaigns(self.server.supervisor.config)
            elif url.path == '/api/file' and set(q) == {'campaign','area','name'}:
                name = q['name'][0]
                data = artifact(self.server.supervisor.config, q['campaign'][0], q['area'][0], name)
                return self.reply(200, data, 'application/octet-stream', name)
            else:
                return self.reply(404, {'error':'Unknown endpoint'})
            self.reply(200, data)
        except FileNotFoundError:
            self.reply(404, {'error':'Artifact not available'})
        except OverflowError as error:
            self.reply(413, {'error':str(error)})
        except (ValueError, TypeError):
            self.reply(400, {'error':'Invalid request or metadata'})
        except OSError:
            self.reply(503, {'error':'Artifact temporarily unavailable'})

    def do_POST(self):
        if not self.guard():
            return
        try:
            self.connection.settimeout(3)
            lengths = self.headers.get_all('Content-Length') or []
            if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 1024 or self.headers.get_content_type() != 'application/json':
                raise ValueError('Invalid request body')
            body = json.loads(self.rfile.read(int(lengths[0])))
            if self.path == '/api/start' and body == {}:
                self.server.supervisor.start()
            elif self.path == '/api/stop' and body == {}:
                self.server.supervisor.stop()
            elif self.path == '/api/resume' and type(body) is dict and set(body) == {'campaign'} and type(body['campaign']) is str and UUID.fullmatch(body['campaign']):
                self.server.supervisor.start(body['campaign'])
            else:
                raise ValueError('Unsupported command/override')
            self.reply(202, self.server.supervisor.status())
        except (ValueError, TypeError):
            self.reply(400, {'error':'Invalid command/configuration'})
        except RuntimeError as error:
            self.reply(409, {'error':str(error)})
        except OSError:
            self.reply(503, {'error':'Configuration/checkpoint unavailable'})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, supervisor, token, port=8766):
        if type(token) is not str or re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token) is None:
            raise ValueError('Use a 32..128 character random ASCII token')
        self.supervisor, self.token = supervisor, token
        super().__init__(('127.0.0.1', port), Handler)


def main(argv=None):
    from .uart_config import load
    p = argparse.ArgumentParser(description='Separate UART panel; binds ONLY to 127.0.0.1')
    p.add_argument('--config', required=True)
    p.add_argument('--seeds', nargs='+', required=True)
    p.add_argument('--port', type=int, default=8766)
    args = p.parse_args(argv)
    supervisor = Supervisor(load(args.config), args.seeds)
    token = os.environ.get('USB_DEVICE_LAB_UART_TOKEN') or secrets.token_urlsafe(32)
    server = Server(supervisor, token, args.port)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print('UART panel http://127.0.0.1:%d / token: %s' % (server.server_port, token), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
        supervisor.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
