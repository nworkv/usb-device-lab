"""Persistent, incremental receive-time event index; never asserts USB causality."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from .dual_web import IDENT, ATTEMPT
from .research_files import open_file, read_json

MARKERS=[('panic',r'kernel panic'),('oops',r'\bOops\b'),('bug',r'\bBUG:'),
         ('sanitizer',r'KASAN:|KMSAN:|KFENCE:|UBSAN:'),('lockup',r'(?:soft|hard) lockup'),('warning',r'\bWARNING:')]

class Registry:
    def __init__(self,config):
        self.config=config;self.lock=threading.RLock()
    def connect(self):
        root=self.config.results_dir;root.mkdir(parents=True,exist_ok=True)
        path=root/'.research-events.sqlite3'
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1:
                raise ValueError('Небезопасный файл реестра событий')
        finally:os.close(fd)
        db=sqlite3.connect(path,timeout=1)
        db.executescript('CREATE TABLE IF NOT EXISTS progress(campaign TEXT PRIMARY KEY,payload TEXT);'
            'CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,campaign TEXT,kind TEXT,payload TEXT);'
            'CREATE INDEX IF NOT EXISTS event_campaign ON events(campaign);'
            'CREATE TABLE IF NOT EXISTS attempts(campaign TEXT,attempt TEXT,start INTEGER,end INTEGER,done INTEGER,PRIMARY KEY(campaign,attempt));'
            'CREATE TABLE IF NOT EXISTS repeats(campaign TEXT,event TEXT,repeat TEXT PRIMARY KEY);')
        return db
    @contextmanager
    def session(self):
        db=self.connect()
        try:
            with db:yield db
        finally:db.close()
    def put(self,db,campaign,kind,source,start,end,mono,message,exact=None,partial=False,end_mono=None):
        ident=hashlib.sha256((campaign+'|'+source+'|'+str(start)).encode()).hexdigest()[:32]
        value=dict(id=ident,campaign=campaign,kind=kind,source=source,start=start,end=end,monotonic_ns=mono,
            monotonic_end_ns=mono if end_mono is None else end_mono,message=message[:4096],message_truncated=len(message)>4096,exact_attempt=exact,partial=partial,causal=False)
        db.execute('INSERT INTO events VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,payload=excluded.payload',
                   (ident,campaign,kind,json.dumps(value,ensure_ascii=False)))
    def tick(self,campaign):
        if type(campaign) is not str or IDENT.fullmatch(campaign) is None:raise ValueError('Некорректный запуск')
        meta=read_json(self.config,campaign,'campaign','campaign.json')
        if meta.get('capture_id')!=campaign or meta.get('mode') not in ('uart','ssh'):raise ValueError('Некорректная идентификация запуска')
        with self.lock,self.session() as db:
            row=db.execute('SELECT payload FROM progress WHERE campaign=?',(campaign,)).fetchone()
            p=json.loads(row[0]) if row else dict(pos=0,raw_end=0,seq=0,mono=0,epoch=0,carry='',base=0,time=0,line_length=0,long_lines=0)
            try:
                with open_file(self.config,campaign,'capture','chunks.jsonl') as index,open_file(self.config,campaign,'capture','uart.raw') as raw:
                    if os.fstat(index.fileno()).st_size<p['pos'] or os.fstat(raw.fileno()).st_size<p['raw_end']:
                        raise ValueError('Захват уменьшился; автоматическое переиндексирование запрещено')
                    index.seek(p['pos']);budget=0
                    for _ in range(256):
                        if budget>=1048576:break
                        begin=index.tell();line=index.readline(65537)
                        if not line or not line.endswith(b'\n'):break
                        if len(line)>65536:raise ValueError('Слишком большая запись индекса')
                        c=json.loads(line);keys=('sequence','offset_start','offset_end','byte_count','monotonic_ns','connection_epoch')
                        if any(type(c.get(k)) is not int for k in keys) or c['sequence']!=p['seq']+1 or c['offset_start']!=p['raw_end'] or not 0<c['byte_count']<=65536 or c['offset_end']!=c['offset_start']+c['byte_count'] or c['monotonic_ns']<p['mono'] or c['connection_epoch']<max(1,p['epoch']):
                            raise ValueError('Нарушена последовательность индекса захвата')
                        raw.seek(c['offset_start']);data=raw.read(c['byte_count'])
                        if len(data)!=c['byte_count']:index.seek(begin);break
                        carry=bytes.fromhex(p['carry']);length=p.get('line_length',0)
                        base=p['base'];when=p['time'];position=c['offset_start']
                        if length and c['connection_epoch']!=p['epoch']:carry=b'';length=0
                        for part in data.splitlines(keepends=True):
                            if not length:base=position;when=c['monotonic_ns']
                            carry=(carry+part)[:8192];length+=len(part)
                            complete=part.endswith(b'\n') or part.endswith(b'\r')
                            text=carry.decode('utf-8',errors='replace').rstrip('\r\n')
                            for kind,pattern in MARKERS:
                                if re.search(pattern,text,re.I):
                                    self.put(db,campaign,kind,'capture',base,base+length,when,text,partial=not complete,end_mono=c['monotonic_ns']);break
                            if complete:
                                if length>8192:p['long_lines']=p.get('long_lines',0)+1
                                carry=b'';length=0
                            position+=len(part)
                        p.update(pos=index.tell(),raw_end=c['offset_end'],seq=c['sequence'],mono=c['monotonic_ns'],epoch=c['connection_epoch'],
                                 carry=carry.hex(),base=base,time=when,line_length=length)
                        budget+=len(data)
            except FileNotFoundError:pass
            root=self.config.results_dir/campaign
            known={a:(done,end) for a,done,end in db.execute('SELECT attempt,done,end FROM attempts WHERE campaign=?',(campaign,))}
            names=sorted(n for n in os.listdir(root) if ATTEMPT.fullmatch(n))
            pending=[n for n in names if not known.get(n,(False,None))[0]][:64]
            for name in pending:
                try:window=read_json(self.config,campaign,name,'window.json');start,end=window['start_ns'],window['end_ns']
                except FileNotFoundError:
                    try:start=read_json(self.config,campaign,name,'started.json')['start_ns'];end=None
                    except FileNotFoundError:continue
                if type(start) is not int or start<0 or end is not None and (type(end) is not int or end<=start):raise ValueError('Некорректное окно попытки')
                done=False
                try:
                    result=read_json(self.config,campaign,name,'result.json');done=end is not None
                    error=result.get('infrastructure_error');rc=result.get('executor_returncode')
                    if error or rc not in (None,0,-15,-9):
                        self.put(db,campaign,'executor_error',name,0,0,start,str(error or 'Код завершения исполнителя: '+str(rc)),exact=name)
                except FileNotFoundError:pass
                db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?)',(campaign,name,start,end,int(done)))
            try:
                source=read_json(self.config,campaign,'campaign','replay-source.json');ref=source.get('event_ref')
                if type(ref) is dict and type(ref.get('campaign')) is str and type(ref.get('event')) is str and IDENT.fullmatch(ref['campaign']) and IDENT.fullmatch(ref['event']):
                    db.execute('INSERT OR IGNORE INTO repeats VALUES(?,?,?)',(ref['campaign'],ref['event'],campaign))
            except FileNotFoundError:pass
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(campaign,json.dumps(p)))
            return dict(indexed_bytes=p['raw_end'],index_records=p['seq'],pending_attempts=max(0,len(names)-sum(v[0] for v in known.values())))
    def enrich(self,db,value):
        exact=value['exact_attempt']
        if exact:matches=[dict(attempt=exact,relation='executor_result',pending=False)]
        else:matches=[dict(attempt=a,relation='receive_window_not_cause',pending=e is None) for a,e in db.execute(
            'SELECT attempt,end FROM attempts WHERE campaign=? AND start<=? AND (end IS NULL OR end>?) ORDER BY start LIMIT 32',
            (value['campaign'],value.get('monotonic_end_ns',value['monotonic_ns']),value['monotonic_ns']))]
        value['attempts']=matches;value['ambiguous']=len(matches)!=1 or any(m['pending'] for m in matches)
        value['repeats']=[r[0] for r in db.execute('SELECT repeat FROM repeats WHERE campaign=? AND event=?',(value['campaign'],value['id']))]
        return value
    def page(self,campaign,before=None,limit=100,kind='all'):
        if type(campaign) is not str or IDENT.fullmatch(campaign) is None or type(limit) is not int or not 1<=limit<=200:raise ValueError('Некорректная страница событий')
        if kind not in {'all','panic','oops','bug','sanitizer','lockup','warning','executor_error'}:raise ValueError('Неизвестный тип события')
        with self.lock,self.session() as db:
            if before is not None and (type(before) is not int or not 0<before<=9223372036854775807):raise ValueError('Некорректный курсор страницы')
            maximum=before if before is not None else 9223372036854775807
            rows=list(db.execute('SELECT rowid,payload FROM events WHERE campaign=? AND rowid<? AND (?=\'all\' OR kind=?) ORDER BY rowid DESC LIMIT ?',(campaign,maximum,kind,kind,limit)))
            progress=db.execute('SELECT payload FROM progress WHERE campaign=?',(campaign,)).fetchone()
            return dict(events=[self.enrich(db,json.loads(v)) for _,v in rows],next_before=rows[-1][0] if len(rows)==limit else None,
                count=db.execute('SELECT COUNT(*) FROM events WHERE campaign=?',(campaign,)).fetchone()[0],
                indexed_bytes=json.loads(progress[0])['raw_end'] if progress else 0,
                long_lines_prefix_only=json.loads(progress[0]).get('long_lines',0) if progress else 0,causal=False)
    def detail(self,campaign,event):
        if type(campaign) is not str or IDENT.fullmatch(campaign) is None or type(event) is not str or IDENT.fullmatch(event) is None:raise ValueError('Некорректное событие')
        with self.lock,self.session() as db:
            row=db.execute('SELECT payload FROM events WHERE campaign=? AND id=?',(campaign,event)).fetchone()
            if not row:raise FileNotFoundError('Событие не найдено')
            value=self.enrich(db,json.loads(row[0]))
        if value['source']=='capture':
            with open_file(self.config,campaign,'capture','uart.raw') as f:
                begin=max(0,value['start']-8192);f.seek(begin);data=f.read(32768)
            value.update(context_start=begin,context_end=begin+len(data),context=data.decode('utf-8',errors='replace'))
        else:
            try:value['executor_result']=read_json(self.config,campaign,value['exact_attempt'],'result.json')
            except FileNotFoundError:value['executor_result']=None
        return value
