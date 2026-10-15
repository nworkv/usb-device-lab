"""Operator panel bound to manifest snapshots, checkpoint engine and bounded analysis."""
import argparse
import json
import os
import re
import secrets
import signal
import sqlite3
import stat
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .uart_review_http import Server as ReviewServer, ReviewHandler
from .uart_web import artifact
from .uart_pipeline_ui import PAGE, SCRIPT

ID = re.compile(r'[0-9a-f]{32}')
PIPE = re.compile(r'pipeline-[0-9a-f]{32}')
LIMIT = 32*1024*1024


def pipeline_file(config, campaign, area, name, metadata_only=False):
    if ID.fullmatch(campaign) is None or PIPE.fullmatch(area) is None or name not in {'pipeline-summary.json','complete.json','analysis-error.json','selection.json','exports.jsonl','reports.jsonl','candidates.sqlite3'} and re.fullmatch(r'report-[0-9]{6}\.json',name) is None:
        raise ValueError('Invalid pipeline artifact')
    directory=os.open(config.results_dir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in (campaign,area):
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValueError('Not regular')
            if metadata_only:return info.st_size
            if info.st_size>LIMIT:raise OverflowError('Artifact exceeds 32 MiB')
            data=f.read(LIMIT+1)
            if len(data)>LIMIT:raise OverflowError('Artifact grew beyond limit')
            return data
    finally:os.close(directory)


def details(config, campaign):
    if ID.fullmatch(campaign) is None:raise ValueError('Invalid campaign')
    metadata=json.loads(artifact(config,campaign,'campaign','campaign.json'))
    if metadata.get('capture_id')!=campaign or metadata.get('mode')!='uart':raise ValueError('Campaign identity')
    root=config.results_dir/campaign
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:names=os.listdir(directory)
    finally:os.close(directory)
    attempts=[]
    for name in sorted(n for n in names if re.fullmatch(r'attempt-[0-9]{6}',n))[-100:]:
        try:result=json.loads(artifact(config,campaign,name,'result.json'))
        except FileNotFoundError:result=None
        attempts.append(dict(attempt_id=name,result=result))
    pipelines=[]
    for name in sorted(n for n in names if PIPE.fullmatch(n))[-20:]:
        entry=dict(name=name,summary=None,error=None,files=[],reports=[],diagnostics=[])
        for filename,key in [('pipeline-summary.json','summary'),('analysis-error.json','error')]:
            try:entry[key]=json.loads(pipeline_file(config,campaign,name,filename))
            except FileNotFoundError:pass
        for filename in ('pipeline-summary.json','analysis-error.json','complete.json','selection.json','exports.jsonl','reports.jsonl','candidates.sqlite3'):
            try:pipeline_file(config,campaign,name,filename,metadata_only=True)
            except FileNotFoundError:continue
            except OverflowError:entry['files'].append(filename);continue
            entry['files'].append(filename)
        database=root/name/'candidates.sqlite3'
        if database.exists():
            if database.is_symlink():raise ValueError('Symlink ledger')
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                for start,payload in db.execute('SELECT start,CASE WHEN length(payload)<=4194304 THEN payload ELSE NULL END FROM reports ORDER BY start DESC LIMIT 10'):
                    if payload is None:
                        entry['reports'].append(dict(offset_start=start,display_error='Report exceeds UI decode limit; download original'))
                        continue
                    report=json.loads(payload)
                    full=report.get('text','');context=report.get('context','')
                    report['display_truncated']=len(full)>65536 or len(context)>16384
                    report['text'],report['context']=full[:65536],context[:16384]
                    entry['reports'].append(report)
                for kind,attempt,payload in db.execute('SELECT kind,attempt,substr(payload,1,16384) FROM diagnostics ORDER BY rowid DESC LIMIT 20'):
                    entry['diagnostics'].append(dict(kind=kind,attempt=attempt,text=payload,display_may_be_truncated=True))
        pipelines.append(entry)
    state='Анализ отсутствует или ещё выполняется'
    if any(p['error'] for p in pipelines):state='Есть ошибка анализа; частичные экспорты не означают общий успех'
    elif any(p['summary'] for p in pipelines):state='Анализ сохранён; полнота UART-наблюдения неизвестна'
    return dict(metadata=metadata,attempts=attempts,pipelines=pipelines,state=state)


class Supervisor:
    def __init__(self,config):
        self.config=config
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.thread,self.receiver=None,None
        self.closing=False
        self.stage='idle'
        self.active,self.preflight,self.selection,self.result=None,None,[],None
        self.execution_error,self.analysis_error=None,None

    def status(self):
        with self.lock:
            completed=0
            if self.active:
                try:
                    value=json.loads(artifact(self.config,self.active,'campaign','checkpoint.json'))
                    if type(value.get('next_iteration')) is int:completed=value['next_iteration']
                except (OSError,ValueError):pass
            return dict(stage=self.stage,busy=self.thread is not None and self.thread.is_alive(),active=self.active,
                completed=completed,preflight=self.preflight,selection=self.selection,result=self.result,
                uart=self.receiver.status() if self.receiver else None,execution_error=self.execution_error,
                analysis_error=self.analysis_error,configured=dict(iterations=self.config.iterations,seconds=self.config.seconds,
                    seed=self.config.seed,families=list(self.config.families),profiles=list(self.config.profiles),port=self.config.uart.port))

    def start(self,campaign=None):
        from .uart_config import load
        with self.lock:
            if self.closing or self.thread is not None and self.thread.is_alive():raise RuntimeError('Campaign or analysis active')
            if load(self.config.path)!=self.config:raise RuntimeError('Configuration changed; restart this panel')
            resume=None
            if campaign is not None:
                artifact(self.config,campaign,'campaign','checkpoint.json')
                resume=self.config.results_dir/campaign/'checkpoint.json'
            self.stop_event=threading.Event()
            self.stage='preparing'
            self.active,self.receiver,self.preflight,self.result=None,None,None,None
            self.execution_error,self.analysis_error=None,None
            self.selection=[]
            self.thread=threading.Thread(target=self.work,args=(resume,),daemon=True,name='uart-pipeline-panel')
            self.thread.start()

    def work(self,resume):
        from .uart_pipeline import select_seeds,prepare_seeds,analyze_capture,dump
        from .uart_preflight import inspect
        from .uart_receiver import UARTReceiver
        from .uart_campaign import run_campaign
        capture=None
        execution=None
        try:
            selection=select_seeds(self.config)
            report=inspect(self.config,[r['path'] for r in selection])
            with self.lock:self.selection=selection;self.preflight=report
            if report['metadata_checks_passed'] is not True:raise RuntimeError('Metadata preflight failed; no USB execution')
            if self.stop_event.is_set():
                with self.lock:self.stage='stopped'
                return
            seeds=prepare_seeds(self.config,selection)
            if self.stop_event.is_set():
                with self.lock:self.stage='stopped'
                return
            def receiver_factory(settings,directory):
                nonlocal capture
                receiver=UARTReceiver(settings,directory);capture=Path(directory)
                with self.lock:self.receiver=receiver;self.active=capture.name;self.stage='running'
                return receiver
            try:
                execution=run_campaign(self.config,seeds,resume=resume,stop=self.stop_event,receiver_factory=receiver_factory)
            except Exception as error:
                with self.lock:self.execution_error=str(error)
            if capture is not None:
                if self.receiver.status()['state'] not in ('stopped','failed'):raise RuntimeError('Capture still active; analysis refused')
                with self.lock:self.stage='analyzing'
                root=self.config.results_dir/capture.name
                dump(root/'selection.json',selection)
                try:analysis=analyze_capture(self.config,root,capture,selection)
                except Exception as error:
                    with self.lock:self.analysis_error=str(error);self.stage='failed'
                    return
                with self.lock:self.result=dict(campaign_id=capture.name,analysis=analysis)
            with self.lock:self.stage='failed' if self.execution_error else 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            with self.lock:self.execution_error=str(error);self.stage='failed'

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.stage!='analyzing' and self.thread is not None and self.thread.is_alive():self.stage='stopping'

    def close(self):
        with self.lock:self.closing=True;thread=self.thread
        self.stop()
        if thread:
            thread.join(30)
            if thread.is_alive():raise RuntimeError('Worker did not stop; inspect stand and analysis')


class Handler(ReviewHandler):
    def do_GET(self):
        if self.path in ('/','/pipeline.js'):
            if not self.guard(auth=False):return
            return self.reply(200,PAGE if self.path=='/' else SCRIPT,'text/html; charset=utf-8' if self.path=='/' else 'text/javascript; charset=utf-8')
        url=urlsplit(self.path)
        if url.path not in ('/api/details','/api/file','/api/campaigns'):return super().do_GET()
        if not self.guard():return
        try:
            q=parse_qs(url.query,keep_blank_values=True,strict_parsing=True,max_num_fields=3)
            if any(len(v)!=1 for v in q.values()):raise ValueError('Duplicate query')
            if url.path=='/api/details' and set(q)=={'campaign'}:return self.reply(200,details(self.server.supervisor.config,q['campaign'][0]))
            if url.path=='/api/file' and set(q)=={'campaign','area','name'} and PIPE.fullmatch(q['area'][0]):
                data=pipeline_file(self.server.supervisor.config,q['campaign'][0],q['area'][0],q['name'][0])
                return self.reply(200,data,'application/octet-stream',q['name'][0])
            if url.path=='/api/campaigns' and not q:
                from .uart_web import campaigns
                rows=campaigns(self.server.supervisor.config)
                for row in rows:row['modified_ns']=(self.server.supervisor.config.results_dir/row['id']).stat().st_mtime_ns
                rows.sort(key=lambda r:r['modified_ns'],reverse=True)
                return self.reply(200,rows)
            if url.path=='/api/file':return super().do_GET()
            raise ValueError('Invalid query')
        except FileNotFoundError:self.reply(404,{'error':'Artifact not yet available'})
        except OverflowError as error:self.reply(413,{'error':str(error)})
        except (ValueError,TypeError,RecursionError,sqlite3.Error):self.reply(400,{'error':'Invalid request or metadata'})
        except OSError:self.reply(503,{'error':'Artifact temporarily unavailable'})


class Server(ReviewServer):
    def __init__(self,supervisor,token,port=8766):
        super().__init__(supervisor,token,port)
        self.RequestHandlerClass=Handler


def main(argv=None):
    from .uart_config import load
    p=argparse.ArgumentParser(description='Manifest UART pipeline operator panel, loopback only')
    p.add_argument('--config',required=True);p.add_argument('--port',type=int,default=8766)
    args=p.parse_args(argv)
    supervisor=Supervisor(load(args.config))
    token=os.environ.get('USB_DEVICE_LAB_UART_TOKEN') or secrets.token_urlsafe(32)
    server=Server(supervisor,token,args.port)
    previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('UART pipeline panel http://127.0.0.1:%d / token: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        signal.signal(signal.SIGTERM,previous);server.server_close();supervisor.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
