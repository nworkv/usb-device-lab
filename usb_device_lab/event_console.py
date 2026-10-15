"""Persistent event cards mounted on the designed research console."""
import json
import os
import secrets
import signal
import threading
from urllib.parse import parse_qs,urlsplit
from .dual_web import IDENT
from .dual_config import load
from .research_service import Supervisor as BaseSupervisor
from .research_frontend import Handler as BaseHandler, Server as BaseServer
from .research_replay import prepare
from .research_files import read_json
from .research_ui import PAGE
from .event_registry import Registry
from .event_ui import PANEL,SCRIPT

class Supervisor(BaseSupervisor):
    def replay(self,campaign,attempt,event_ref=None):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Повтор запрещён во время запуска или анализа')
            if load(self.config.path,self.config.telemetry_mode)!=self.config:raise RuntimeError('Конфигурация изменилась; перезапустите панель')
            raw,config,source=prepare(self.config,campaign,attempt)
            if event_ref:source['event_ref']=event_ref
            self.stop_event=threading.Event();self.active,self.receiver,self.result,self.error=None,None,None,None
            self.replay_source=source;self.stage='preparing'
            self.thread=threading.Thread(target=self.repeat_work,args=(config,raw,source),name='event-replay',daemon=True);self.thread.start()

class Indexer:
    def __init__(self,server):
        self.server=server;self.stop=threading.Event();self.lock=threading.Lock();self.stores={};self.requested=[];self.errors={};self.position=0
        self.thread=threading.Thread(target=self.work,name='persistent-event-index',daemon=True);self.thread.start()
    def registry(self):
        config=self.server.supervisor.config;key=str(config.results_dir)
        with self.lock:
            if key not in self.stores:self.stores[key]=Registry(config)
            return self.stores[key]
    def request(self,campaign):
        if type(campaign) is not str or IDENT.fullmatch(campaign) is None:raise ValueError('Некорректный запуск')
        with self.lock:self.requested=([campaign]+[c for c in self.requested if c!=campaign])[:4]
    def work(self):
        while not self.stop.wait(0.5):
            try:
                registry=self.registry();root=registry.config.results_dir
                recent=[]
                if root.exists():
                    for entry in os.scandir(root):
                        if IDENT.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):recent.append((entry.stat(follow_symlinks=False).st_mtime_ns,entry.name))
                recent=[c for _,c in sorted(recent,reverse=True)[:200]]
                with self.lock:chosen=list(self.requested)
                active=self.server.supervisor.active
                if active:chosen.insert(0,active)
                if recent:self.position=(self.position+1)%len(recent);chosen.append(recent[self.position])
                for campaign in dict.fromkeys(chosen):
                    if self.stop.is_set():break
                    try:registry.tick(campaign);self.errors.pop(campaign,None)
                    except Exception as error:self.errors[campaign]=str(error)
            except Exception as error:self.errors['worker']=str(error)
    def close(self):self.stop.set();self.thread.join(3)

class Handler(BaseHandler):
    def do_GET(self):
        url=urlsplit(self.path)
        if url.path in ('/','/events.js'):
            if not self.guard(auth=False):return
            if self.path!=url.path:return self.reply(400,{'error':'Параметры страницы не допускаются'})
            if url.path=='/events.js':return self.reply(200,SCRIPT,'text/javascript; charset=utf-8')
            marker=b'<section id="results" class="page" hidden>'
            if PAGE.count(marker)!=1:raise RuntimeError('Версия основной страницы изменилась')
            page=PAGE.replace(marker,marker+PANEL,1).replace(b'<script src="/research.js"></script>',b'<script src="/research.js"></script><script src="/events.js"></script>',1)
            return self.reply(200,page,'text/html; charset=utf-8')
        if url.path not in ('/api/events','/api/event','/api/event-download'):return super().do_GET()
        if not self.guard():return
        try:
            q=parse_qs(url.query,strict_parsing=True,max_num_fields=4)
            if any(len(v)!=1 for v in q.values()):raise ValueError('Повтор параметра')
            q={k:v[0] for k,v in q.items()};campaign=q['campaign'];self.server.indexer.request(campaign)
            registry=self.server.indexer.registry()
            if url.path=='/api/events':
                if q.keys()-{'campaign','before','limit','kind'}:raise ValueError('Параметры списка событий')
                value=registry.page(campaign,int(q['before']) if 'before' in q else None,int(q.get('limit','100')),q.get('kind','all'))
                value['index_error']=self.server.indexer.errors.get(campaign) or self.server.indexer.errors.get('worker')
                return self.reply(200,value)
            if set(q)!={'campaign','event'}:raise ValueError('Укажите событие')
            value=registry.detail(campaign,q['event'])
            value['repeat_results']=[]
            for repeat in value['repeats'][-50:]:
                try:result=read_json(registry.config,repeat,'campaign','replay-result.json')
                except FileNotFoundError:result=None
                value['repeat_results'].append(dict(campaign=repeat,result=result))
            if url.path=='/api/event-download':return self.reply(200,json.dumps(value,ensure_ascii=False,indent=2).encode(),'application/json',campaign+'-'+q['event']+'.json')
            return self.reply(200,value)
        except FileNotFoundError:self.reply(404,{'error':'Событие или исходные материалы пока не найдены'})
        except (ValueError,TypeError,KeyError,OverflowError) as error:self.reply(400,{'error':str(error)})
        except Exception:self.reply(503,{'error':'Реестр временно недоступен; проверьте индексирование'})
    def do_POST(self):
        if self.path!='/api/replay-event':return super().do_POST()
        if not self.guard():return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=4096 or self.headers.get('Content-Type')!='application/json':raise ValueError('Требуется ограниченный JSON')
            value=json.loads(self.rfile.read(size))
            if type(value) is not dict or set(value)!={'campaign','event','attempt'}:raise ValueError('Укажите событие и выбранную попытку')
            event=self.server.indexer.registry().detail(value['campaign'],value['event'])
            allowed=[a['attempt'] for a in event['attempts'] if not a['pending']]
            if value['attempt'] not in allowed:raise ValueError('Попытка не связана с событием завершённым окном; используйте отдельный ручной повтор')
            self.server.supervisor.replay(value['campaign'],value['attempt'],event_ref=dict(campaign=value['campaign'],event=value['event']))
            self.reply(200,self.server.supervisor.status())
        except RuntimeError as error:self.reply(409,{'error':str(error)})
        except (ValueError,TypeError,KeyError,OSError) as error:self.reply(400,{'error':str(error)})

class Server(BaseServer):
    def __init__(self,supervisor,token,port=8766):
        super().__init__(supervisor,token,port);self.RequestHandlerClass=Handler;self.indexer=Indexer(self)
    def server_close(self):
        if hasattr(self,'indexer'):self.indexer.close()
        super().server_close()

def serve(config,port=8766):
    supervisor=Supervisor(config);token=os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server=Server(supervisor,token,port);previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:signal.signal(signal.SIGTERM,previous);supervisor.close();server.server_close()
