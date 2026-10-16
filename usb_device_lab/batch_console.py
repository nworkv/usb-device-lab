"""Preview-bound batch execution in the existing generation/research console."""
import copy
from dataclasses import replace
import json
import os
import secrets
import signal
import threading
from urllib.parse import urlsplit
import uuid
from .generation_console import Supervisor as BaseSupervisor, Handler as BaseHandler, Server as BaseServer
from .batch_campaign import make_plan, run_batch
from .campaign_options import apply_options
from .corpus_selection import apply_family
from .dual_config import load
from .dual_pipeline import run_complete


class Supervisor(BaseSupervisor):
    def __init__(self, config):
        super().__init__(config)
        self.batch = None
        self.batch_preview = None
        self.batch_display = False

    def candidate(self, all_groups):
        config = apply_options(self.corpus_config(),self.options)
        return replace(config,families=(),profiles=()) if all_groups else apply_family(config,self.corpus_family)

    def plan_batch(self, all_groups):
        if type(all_groups) is not bool:raise ValueError('all_groups должен быть boolean')
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Сначала дождитесь завершения работы')
            config = self.candidate(all_groups)
            groups = make_plan(config)
            token = uuid.uuid4().hex
            self.batch_preview = dict(token=token,config=config,groups=groups,all_groups=all_groups)
            return dict(token=token,mode=config.telemetry_mode,udc=config.udc,seconds=config.seconds,
                pre=config.pre_run_quiet_seconds,post=config.post_run_capture_seconds,
                groups=[dict(family=g['family'],attempts=len(g['selection'])) for g in groups],
                planned_attempts=sum(len(g['selection']) for g in groups))

    def start_batch(self, token):
        with self.lock:
            if self.closing or self.busy():raise RuntimeError('Запуск или другая операция уже выполняется')
            preview = self.batch_preview
            if type(token) is not str or not preview or token!=preview['token']:raise ValueError('Постройте новый план')
            config = self.candidate(preview['all_groups'])
            if config!=preview['config'] or load(self.config.path,self.config.telemetry_mode)!=self.config:
                raise ValueError('Настройки изменились; постройте новый план')
            self.batch_preview = None
            self.stop_event,self.skip_event = threading.Event(),threading.Event()
            self.active,self.receiver,self.result,self.error,self.replay_source = None,None,None,None,None
            self.batch = dict(state='preparing',planned_attempts=sum(len(g['selection']) for g in preview['groups']),groups=[],completed_groups=0)
            self.batch_display,self.stage = True,'preparing'
            self.thread = threading.Thread(target=self.batch_work,args=(config,copy.deepcopy(preview['groups'])),name='batch-operator',daemon=True)
            try:self.thread.start()
            except Exception:
                self.stage='failed';self.batch['state']='failed'
                raise

    def batch_work(self, config, groups):
        def notify(value):
            with self.lock:self.batch = copy.deepcopy(value)
        def stage(value):
            with self.lock:self.stage = value
        def pipeline(group_config, **kwargs):
            publish_capture = kwargs.pop('on_capture')
            def capture(receiver,directory):
                with self.lock:self.receiver,self.active = receiver,directory.name
                publish_capture(receiver,directory)
            return run_complete(group_config,on_capture=capture,on_stage=stage,**kwargs)
        try:
            result = run_batch(config,stop=self.stop_event,skip=self.skip_event,pipeline=pipeline,
                on_progress=notify,expected_plan=groups)
            with self.lock:
                self.batch,self.result = result,result
                self.error = result.get('error')
                self.stage = result['state'] if result['state'] in ('completed','stopped','review_required') else 'failed'
        except Exception as error:
            with self.lock:
                self.error,self.stage = str(error),'failed'
                self.batch.update(state='failed',error=str(error))

    def status(self):
        with self.lock:
            value = super().status()
            value['batch'] = copy.deepcopy(self.batch)
            if self.batch_display and self.batch and not self.generation_busy():
                rows = self.batch.get('groups',[])
                done = sum(row['planned'] for row in rows if row['state']=='completed')
                if rows and rows[-1]['state']!='completed' and rows[-1].get('campaign_id')==self.active and self.active:
                    done += min(value['completed'],rows[-1]['planned'])
                value.update(iterations=self.batch['planned_attempts'],completed=done)
            return value

    def start(self, ident=None):
        with self.lock:
            result=super().start(ident);self.batch_display=False;return result

    def replay(self, campaign, attempt, event_ref=None):
        with self.lock:
            result=super().replay(campaign,attempt,event_ref);self.batch_display=False;return result

    def select(self, mode):
        with self.lock:
            result=super().select(mode);self.batch_display=False;return result

    def start_build(self, raw):
        with self.lock:
            result=super().start_build(raw);self.batch_display=False;return result


SCRIPT = """'use strict';
stages.review_required='Требуется проверка стенда';
const batchPanel=node('article','','glass'),batchAll=document.createElement('input'),batchPlanButton=node('button','Построить пакетный план'),batchStartButton=node('button','Запустить пакет'),batchPreviewBox=node('pre',''),batchInfo=node('div','');
batchAll.type='checkbox';batchAll.checked=true;const batchLabel=node('label','Все группы активного корпуса ');batchLabel.prepend(batchAll);batchPanel.append(node('h2','Пакетный запуск'),batchLabel,batchPlanButton,batchStartButton,batchPreviewBox,batchInfo,node('p','Один запуск каждого существующего seed. Бюджет обычной кампании не используется. Остановка и пропуск одной попытки — общими кнопками. При ошибке или кандидатах ядра следующая группа не запускается. Batch-resume пока отсутствует: новый старт повторяет выбранные seeds с начала.','muted'));el('run').append(batchPanel);
let batchPreview=null,batchEpoch=-1,batchPlanning=false,batchDrawn='';
batchAll.onchange=()=>{batchPreview=null;batchPreviewBox.textContent='Постройте новый план';};
batchPlanButton.onclick=async()=>{if(!token||batchPlanning)return;const gen=epoch;batchEpoch=epoch;batchPlanning=true;try{const value=await api('/api/batch-plan',{all_groups:batchAll.checked});if(gen!==epoch)return;batchPreview=value;batchPreviewBox.textContent=JSON.stringify({mode:value.mode,udc:value.udc,seconds:value.seconds,pre:value.pre,post:value.post,planned_attempts:value.planned_attempts,groups:value.groups},null,2);}catch(error){if(gen===epoch){batchPreview=null;el('error').textContent=error.message;}}finally{if(gen===epoch)batchPlanning=false;}};
batchStartButton.onclick=()=>{if(!batchPreview)return;if(confirm('Запустить '+batchPreview.planned_attempts+' USB-попыток из показанного плана на выделенном стенде?')){const token=batchPreview.token;batchPreview=null;action('/api/batch-start',{token});}};
setInterval(()=>{if(batchEpoch!==epoch){batchEpoch=epoch;batchPreview=null;batchPlanning=false;batchDrawn='';batchPreviewBox.textContent='';batchInfo.replaceChildren();}const blocked=!token||!state||Boolean(state.busy)||batchPlanning;batchPlanButton.disabled=batchAll.disabled=blocked;batchStartButton.disabled=blocked||!batchPreview;if(!token)return;const value=state?.batch,text=JSON.stringify(value);if(text===batchDrawn)return;batchDrawn=text;batchInfo.replaceChildren();if(!value)return;batchInfo.append(node('p','Пакет: '+value.state+' · завершено групп: '+(value.completed_groups||0)+' · запланировано: '+value.planned_attempts));for(const row of value.groups||[])batchInfo.append(node('p',row.family+' · '+row.state+' · записано результатов: '+(row.attempts_recorded??'—')+' · пропущено: '+(row.skipped_attempts??'—')+' · запуск: '+(row.campaign_id||'ещё не создан')));if(value.error)batchInfo.append(node('p',value.error,'error'));},500);
""".encode('utf-8')


class Handler(BaseHandler):
    def reply(self, code, value, kind='application/json', filename=None):
        if self.path=='/' and code==200 and kind.startswith('text/html') and isinstance(value,bytes):
            marker=b'<script src="/generation.js"></script>'
            if value.count(marker)!=1:raise RuntimeError('Версия страницы изменилась')
            value=value.replace(marker,marker+b'<script src="/batch.js"></script>',1)
        return super().reply(code,value,kind,filename)

    def do_GET(self):
        url=urlsplit(self.path)
        if url.path!='/batch.js':return super().do_GET()
        if not self.guard(auth=False):return
        if self.path!=url.path:return self.reply(400,{'error':'Параметры скрипта не допускаются'})
        self.reply(200,SCRIPT,'text/javascript; charset=utf-8')

    def do_POST(self):
        if self.path not in ('/api/batch-plan','/api/batch-start'):return super().do_POST()
        if not self.guard():return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=4096 or self.headers.get('Content-Type')!='application/json':raise ValueError('Требуется ограниченный JSON')
            value=json.loads(self.rfile.read(size))
            if type(value) is not dict:raise ValueError('Требуется JSON-объект')
            if self.path=='/api/batch-plan' and set(value)=={'all_groups'}:
                return self.reply(200,self.server.supervisor.plan_batch(value['all_groups']))
            if self.path=='/api/batch-start' and set(value)=={'token'}:
                self.server.supervisor.start_batch(value['token'])
                return self.reply(200,self.server.supervisor.status())
            raise ValueError('Некорректные поля запроса')
        except RuntimeError as error:self.reply(409,{'error':str(error)})
        except (ValueError,TypeError,KeyError,OSError) as error:self.reply(400,{'error':str(error)})


class Server(BaseServer):
    def __init__(self,supervisor,token,port=8766):
        super().__init__(supervisor,token,port);self.RequestHandlerClass=Handler


def serve(config,port=8766):
    supervisor=Supervisor(config);token=os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server=Server(supervisor,token,port)
    previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        signal.signal(signal.SIGTERM,previous)
        try:supervisor.close()
        finally:server.server_close()
