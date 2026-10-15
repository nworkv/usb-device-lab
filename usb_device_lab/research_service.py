"""Research API over the existing loopback/Bearer guard; UI follows separately."""
import hashlib
import json
import os
import secrets
import signal
import tempfile
import threading
import zipfile
from urllib.parse import parse_qs,urlsplit
from .dual_web import Handler as BaseHandler, Server as BaseServer, Supervisor as BaseSupervisor
from .dual_config import load
from .research_files import open_file, read_json, preview, export_paths
from .research_replay import prepare, run


class Supervisor(BaseSupervisor):
    def __init__(self,config):
        super().__init__(config);self.replay_source=None
    def status(self):
        value=super().status()
        with self.lock:
            value.update(replay_source=self.replay_source,seconds=self.replay_source['seconds'] if self.replay_source else self.config.seconds,
                seed=self.config.seed,families=list(self.config.families),profiles=list(self.config.profiles))
            if self.replay_source:value['iterations']=1
        return value
    def start(self,ident=None):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Запуск или анализ уже выполняется')
            super().start(ident);self.replay_source=None
    def replay(self,campaign,attempt):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Повтор запрещён во время запуска или анализа')
            if load(self.config.path,self.config.telemetry_mode)!=self.config:raise RuntimeError('Конфигурация изменилась; перезапустите панель')
            raw,config,source=prepare(self.config,campaign,attempt)
            self.stop_event=threading.Event();self.active,self.receiver,self.result,self.error=None,None,None,None
            self.replay_source=source;self.stage='preparing'
            self.thread=threading.Thread(target=self.repeat_work,args=(config,raw,source),name='research-replay',daemon=True)
            self.thread.start()
    def repeat_work(self,config,raw,source):
        def capture(receiver,directory):
            with self.lock:self.receiver,self.active=receiver,directory.name
        def stage(value):
            with self.lock:self.stage=value
        try:
            result=run(config,raw,source,self.stop_event,capture,stage)
            with self.lock:
                self.result=result;self.error=result.get('execution_error') or result.get('analysis_error')
                self.stage='failed' if self.error else 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            with self.lock:self.error,self.stage=str(error),'failed'


class Handler(BaseHandler):
    def send_stream(self,stream,name,start=0,end=None):
        size=os.fstat(stream.fileno()).st_size;end=size if end is None else end
        if not 0<=start<=end<=size:raise ValueError('Диапазон вне файла')
        self.send_response(200);self.send_header('Content-Type','application/octet-stream')
        self.send_header('Content-Length',str(end-start));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Disposition','attachment; filename="'+name+'"');self.end_headers()
        self._stream_started=True;stream.seek(start);remaining=end-start
        while remaining:
            chunk=stream.read(min(65536,remaining))
            if not chunk:raise OSError('Файл уменьшился во время выдачи')
            self.wfile.write(chunk);remaining-=len(chunk)
    def bundle(self,config,campaign):
        if self.server.supervisor.busy():raise RuntimeError('Сначала остановите попытки и дождитесь анализа')
        if not self.server.export_lock.acquire(blocking=False):raise RuntimeError('Уже формируется пакет; повторите позднее')
        try:
            manifest=dict(campaign=campaign,files=[],missing=[],atomic=False,completeness='unknown',causal=False)
            total=0
            with tempfile.TemporaryFile() as temporary:
                with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
                    for index,(area,name) in enumerate(export_paths(config,campaign)):
                        if index>=4096:raise OverflowError('Пакет превышает 4096 файлов; скачайте отдельно')
                        try:source=open_file(config,campaign,area,name)
                        except FileNotFoundError:manifest['missing'].append(area+'/'+name);continue
                        with source:
                            length=os.fstat(source.fileno()).st_size;total+=length
                            if total>128*1024*1024:raise OverflowError('Пакет больше 128 MiB; скачайте большие файлы отдельно')
                            digest=hashlib.sha256();remaining=length
                            with archive.open(area+'/'+name,'w') as destination:
                                while remaining:
                                    chunk=source.read(min(65536,remaining))
                                    if not chunk:raise OSError('Файл изменился во время архивирования')
                                    destination.write(chunk);digest.update(chunk);remaining-=len(chunk)
                            manifest['files'].append(dict(path=area+'/'+name,bytes=length,sha256=digest.hexdigest()))
                    archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                temporary.flush();self.send_stream(temporary,campaign+'-research.zip')
        finally:self.server.export_lock.release()
    def do_GET(self):
        url=urlsplit(self.path)
        if url.path not in ('/api/log','/api/fragment','/api/file','/api/bundle','/api/replay-info'):return super().do_GET()
        if not self.guard():return
        self._stream_started=False
        try:
            q=parse_qs(url.query,keep_blank_values=True,strict_parsing=True,max_num_fields=9)
            if any(len(v)!=1 for v in q.values()):raise ValueError('Повтор параметра')
            q={k:v[0] for k,v in q.items()};config=self.server.supervisor.config
            if url.path in ('/api/log','/api/fragment'):
                if not {'campaign','area','name'}<=q.keys() or q.keys()-{'campaign','area','name','offset','limit','lines','level','query'}:raise ValueError('Параметры журнала')
                value=preview(config,q['campaign'],q['area'],q['name'],offset=int(q['offset']) if 'offset' in q else None,
                    limit=int(q.get('limit','65536')),lines=int(q.get('lines','300')),level=q.get('level','all'),query=q.get('query',''))
                value.update(campaign=q['campaign'],area=q['area'],name=q['name'])
                if url.path=='/api/fragment':return self.reply(200,json.dumps(value,ensure_ascii=False,indent=2).encode(),'application/json',q['campaign']+'-fragment.json')
                return self.reply(200,value)
            if url.path=='/api/file':
                if not {'campaign','area','name'}<=q.keys() or q.keys()-{'campaign','area','name','start','end'}:raise ValueError('Параметры файла')
                with open_file(config,q['campaign'],q['area'],q['name']) as f:
                    return self.send_stream(f,q['campaign']+'-'+q['name'],int(q.get('start','0')),int(q['end']) if 'end' in q else None)
            if set(q)!={'campaign'}:raise ValueError('Укажите запуск')
            if url.path=='/api/bundle':return self.bundle(config,q['campaign'])
            value={}
            for name in ('replay-source.json','replay-result.json'):
                try:value[name]=read_json(config,q['campaign'],'campaign',name)
                except FileNotFoundError:value[name]=None
            return self.reply(200,value)
        except FileNotFoundError:self.reply(404,{'error':'Файл ещё не создан или отсутствует'})
        except OverflowError as error:self.reply(413,{'error':str(error)})
        except RuntimeError as error:self.reply(409,{'error':str(error)})
        except (ValueError,TypeError,KeyError) as error:self.reply(400,{'error':str(error)})
        except OSError:
            if self._stream_started:self.close_connection=True
            else:self.reply(503,{'error':'Файл временно недоступен'})
    def do_POST(self):
        if self.path!='/api/replay':return super().do_POST()
        if not self.guard():return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=4096 or self.headers.get('Content-Type')!='application/json':raise ValueError('Требуется ограниченный JSON')
            value=json.loads(self.rfile.read(size))
            if type(value) is not dict or set(value)!={'campaign','attempt'}:raise ValueError('Укажите исходный запуск и попытку')
            self.server.supervisor.replay(value['campaign'],value['attempt']);self.reply(200,self.server.supervisor.status())
        except RuntimeError as error:self.reply(409,{'error':str(error)})
        except OverflowError as error:self.reply(413,{'error':str(error)})
        except (ValueError,TypeError,KeyError,OSError) as error:self.reply(400,{'error':str(error)})


class Server(BaseServer):
    def __init__(self,supervisor,token,port=8766):
        self.export_lock=threading.Lock();super().__init__(supervisor,token,port);self.RequestHandlerClass=Handler


def serve(config,port=8766):
    supervisor=Supervisor(config);token=os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server=Server(supervisor,token,port)
    previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:signal.signal(signal.SIGTERM,previous);server.server_close();supervisor.close()
