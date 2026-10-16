"""Bounded background corpus generation and explicit activation in the research UI."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import threading
from urllib.parse import urlsplit
import uuid
from .skip_console import Supervisor as BaseSupervisor, Handler as BaseHandler, Server as BaseServer
from .event_console import PAGE, PANEL
from .corpus_variants import build as build_variants, source_profiles
from .extended_corpus import build as build_extended
from .corpus_selection import catalogue, apply_family
from .campaign_options import apply_options
from .dual_pipeline import run_complete


def parameters(value):
    if type(value) is not dict or set(value)!={'preset','count','seed'}:
        raise ValueError('Укажите preset, count и seed')
    if value['preset'] not in ('standard','extended') or type(value['count']) is not int or not 1<=value['count']<=50:
        raise ValueError('preset: standard/extended; count: 1..50')
    if type(value['seed']) is not int or not 0<=value['seed']<2**63:
        raise ValueError('seed: целое число 0..2**63-1')
    return dict(value)


class Supervisor(BaseSupervisor):
    def __init__(self, config):
        super().__init__(config)
        self.generation_worker = None
        self.generation = dict(state='idle',id=None)
        self.generated = None

    def generation_busy(self):
        worker = getattr(self,'generation_worker',None)
        return worker is not None and worker.is_alive()

    def busy(self):
        return super().busy() or self.generation_busy()

    def corpus_config(self):
        active = self.generated
        return replace(self.config,seed_dir=Path(active['output'])/'seeds',manifest=Path(active['output'])/'manifest.toml',families=(active['family'],),profiles=()) if active else self.config

    def corpus_catalogue(self):
        with self.lock:
            return catalogue(self.corpus_config())

    def set_corpus_family(self, family):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Корпус занят')
            apply_family(self.corpus_config(),family)
            self.corpus_family = family

    def status(self):
        with self.lock:
            value = super().status()
            value.update(generation=copy.deepcopy(self.generation),corpus_source=self.generated['id'] if self.generated else 'toml')
            if self.generation_busy():value.update(stage='generating_corpus',completed=0)
            if not self.replay_source:
                config = self.corpus_config()
                family = self.corpus_family
                value.update(families=list(config.families) if family is None else [family] if family else [],profiles=list(config.profiles) if family is None else [])
            return value

    def start_build(self, raw):
        values = parameters(raw)
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Запуск, анализ или генерация уже выполняется')
            ident = uuid.uuid4().hex
            self.generation = dict(state='building',phase='checking',id=ident,parameters=values,total=None,error=None)
            self.generation_worker = threading.Thread(target=self.build_work,args=(self.config,ident,values),name='corpus-build',daemon=True)
            try:self.generation_worker.start()
            except Exception:
                self.generation.update(state='failed',error='Не удалось запустить поток генерации')
                raise

    def build_work(self, config, ident, values):
        try:
            count = len(source_profiles(config.seed_dir,config.manifest)) + (3 if values['preset']=='extended' else 0)
            total = count*values['count']
            if total>2048:raise ValueError('Генерация из панели ограничена 2048 конфигурациями')
            parent = config.corpus_out/'generated'
            parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
                raise ValueError('Небезопасный каталог generated')
            root = parent/ident
            root.mkdir(mode=0o700)
            seed_dir,manifest = config.seed_dir,config.manifest
            with self.lock:self.generation.update(phase='building',total=total)
            if values['preset']=='extended':
                build_extended(seed_dir,manifest,root/'baseline')
                seed_dir,manifest = root/'baseline/seeds',root/'baseline/manifest.toml'
            output = root/'variants'
            result = build_variants(seed_dir,manifest,output,count=values['count'],seed=values['seed'])
            marker = json.loads((output/'complete.json').read_text(encoding='utf-8'))
            if result['configurations']!=total or marker['configurations']!=total:
                raise RuntimeError('Число созданных конфигураций не соответствует плану')
            with self.lock:self.generation.update(state='completed',phase='completed',output=str(output),result=result)
        except Exception as error:
            with self.lock:self.generation.update(state='failed',phase='failed',error=str(error))

    def activate(self, ident):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Корпус нельзя переключить во время работы')
            if ident is None:
                self.generated = None
            else:
                if type(ident) is not str or self.generation.get('state')!='completed' or ident!=self.generation.get('id'):
                    raise ValueError('Укажите завершённую генерацию текущего сеанса')
                output = Path(self.generation['output'])
                if not (output/'complete.json').is_file():raise ValueError('Маркер завершения отсутствует')
                config = replace(self.config,seed_dir=output/'seeds',manifest=output/'manifest.toml',families=(),profiles=())
                first = catalogue(config)['families'][0]['name']
                self.generated = dict(id=ident,output=str(output),family=first)
            self.corpus_family = None

    def work(self, resume):
        def capture(receiver,directory):
            with self.lock:self.receiver,self.active = receiver,directory.name
        def stage(value):
            with self.lock:self.stage = value
        try:
            with self.lock:
                config = apply_family(apply_options(self.corpus_config(),self.options),self.corpus_family)
            result = run_complete(config,resume=resume,stop=self.stop_event,skip=self.skip_event,on_capture=capture,on_stage=stage)
            with self.lock:
                self.result = result
                self.error = result.get('execution_error') or result.get('analysis_error')
                self.stage = 'failed' if self.error else 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            with self.lock:self.error,self.stage = str(error),'failed'

    def stop(self):
        if self.generation_busy():raise RuntimeError('Генерация не отменяется; дождитесь результата. USB-активность не запущена')
        super().stop()

    def close(self):
        with self.lock:self.closing = True;worker = self.generation_worker
        if worker is not None and worker.ident is not None:
            worker.join(30)
            if worker.is_alive():raise RuntimeError('Генерация ещё выполняется; проверьте неполный каталог перед повтором')
        super().close()


SCRIPT = """'use strict';
stages.generating_corpus='Генерация корпуса';
const generationPanel=node('article','','glass'),generationPreset=document.createElement('select'),generationCount=document.createElement('input'),generationSeed=document.createElement('input'),generationCreate=node('button','Создать корпус'),generationActivate=node('button','Активировать результат'),generationReset=node('button','Вернуть корпус из TOML'),generationInfo=node('p','','muted');
for(const [value,text] of [['standard','Все базовые профили манифеста TOML'],['extended','Базовые + Low Speed HID и vendor loopback']]){const option=node('option',text);option.value=value;generationPreset.append(option);}
generationPreset.setAttribute('aria-label','Набор базовых профилей');generationCount.type=generationSeed.type='number';generationCount.min='1';generationCount.max='50';generationCount.value='50';generationSeed.min='0';generationSeed.max='9007199254740991';generationSeed.value='42';generationCount.setAttribute('aria-label','Конфигураций на профиль, включая исходную');generationSeed.setAttribute('aria-label','Seed генерации');
generationPanel.append(node('h2','Создание корпуса'),generationPreset,node('label','Конфигураций на профиль'),generationCount,node('label','Seed'),generationSeed,generationCreate,generationActivate,generationReset,generationInfo,node('p','Из исходного корпуса TOML, в отдельный каталог. Не изменяет TOML и не запускает USB. Генерация не отменяется; дождитесь завершения. Неполные результаты не активируются. Активация выбирает начальную группу, а не все seeds большого корпуса.','muted'));el('run').append(generationPanel);
generationCreate.onclick=async()=>{const count=Number(generationCount.value),seed=Number(generationSeed.value);if(!token||!Number.isInteger(count)||count<1||count>50||!Number.isSafeInteger(seed)||seed<0){el('error').textContent='Проверьте число вариантов и seed';return;}if(confirm('Создать отдельный корпус: '+count+' конфигураций на профиль? USB запускаться не будет.'))action('/api/corpus-build',{preset:generationPreset.value,count,seed});};
async function generationSwitch(job){try{const value=await api('/api/corpus-activate',{job});epoch++;abort.abort();abort=new AbortController();busy=false;state=value;await refresh();}catch(error){el('error').textContent=error.message;}}
generationActivate.onclick=()=>generationSwitch(state?.generation?.id);generationReset.onclick=()=>generationSwitch(null);
setInterval(()=>{const record=state?.generation,blocked=!token||!state||Boolean(state.busy);generationCreate.disabled=generationPreset.disabled=generationCount.disabled=generationSeed.disabled=generationReset.disabled=blocked;generationActivate.disabled=blocked||record?.state!=='completed';if(record?.state==='building')el('stop').disabled=true;generationInfo.textContent=!token?'':!record||record.state==='idle'?'Генерация ещё не запускалась':record.state==='failed'?'Ошибка генерации: '+record.error:record.state==='completed'?'Готово: '+record.result.configurations+' конфигураций. Активация не запускает кампанию.':'Генерация: '+record.phase+(record.total?' · план '+record.total+' конфигураций':'');if(typeof corpusSelect!=='undefined'&&corpusSelect.options.length)corpusSelect.options[0].textContent=state?.corpus_source&&state.corpus_source!=='toml'?'Начальная группа активного корпуса':'Исходный выбор из TOML';},500);
""".encode('utf-8')


class Handler(BaseHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path not in ('/','/generation.js'):return super().do_GET()
        if not self.guard(auth=False):return
        if self.path!=url.path:return self.reply(400,{'error':'Параметры страницы не допускаются'})
        if url.path=='/generation.js':return self.reply(200,SCRIPT,'text/javascript; charset=utf-8')
        marker = b'<section id="results" class="page" hidden>'
        scripts = b'<script src="/research.js"></script>'
        stop = b'<button id="stop" class="danger">'
        if any(PAGE.count(value)!=1 for value in (marker,scripts,stop)):raise RuntimeError('Версия страницы изменилась')
        button = '<button id="skip-attempt" disabled>Пропустить одну попытку</button>'.encode('utf-8')
        page = PAGE.replace(marker,marker+PANEL,1).replace(stop,button+stop,1)
        page = page.replace(scripts,scripts+b'<script src="/events.js"></script><script src="/skip.js"></script><script src="/generation.js"></script>',1)
        return self.reply(200,page,'text/html; charset=utf-8')

    def do_POST(self):
        if self.path not in ('/api/corpus-build','/api/corpus-activate'):return super().do_POST()
        if not self.guard():return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 1<=size<=4096 or self.headers.get('Content-Type')!='application/json':raise ValueError('Требуется ограниченный JSON')
            value = json.loads(self.rfile.read(size))
            if self.path=='/api/corpus-build':self.server.supervisor.start_build(value)
            else:
                if type(value) is not dict or set(value)!={'job'}:raise ValueError('Укажите job или null для исходного корпуса')
                self.server.supervisor.activate(value['job'])
            self.reply(200,self.server.supervisor.status())
        except RuntimeError as error:self.reply(409,{'error':str(error)})
        except (ValueError,TypeError,OSError) as error:self.reply(400,{'error':str(error)})


class Server(BaseServer):
    def __init__(self,supervisor,token,port=8766):
        super().__init__(supervisor,token,port);self.RequestHandlerClass = Handler


def serve(config,port=8766):
    supervisor = Supervisor(config)
    token = os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server = Server(supervisor,token,port)
    previous = signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        signal.signal(signal.SIGTERM,previous)
        try:supervisor.close()
        finally:server.server_close()
