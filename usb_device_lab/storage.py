import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

def atomic_write(path,text):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temp,'w',encoding='utf-8') as f: f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally: temp.unlink(missing_ok=True)

class Store:
    def __init__(self,directory):
        self.root=Path(directory); self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'runs.sqlite3',timeout=30); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,started REAL,finished REAL,digest TEXT,status TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS coverage(namespace TEXT,pc TEXT,PRIMARY KEY(namespace,pc));
        CREATE TABLE IF NOT EXISTS corpus(digest TEXT PRIMARY KEY,added REAL);
        CREATE TABLE IF NOT EXISTS errors(signature TEXT PRIMARY KEY,kind TEXT,first_run TEXT,last_run TEXT,occurrences INTEGER);
        '''); self.db.commit()
    def seed(self,config):
        atomic_write(self.root/'corpus'/(config.digest+'.json'),config.canonical())
        with self.db: self.db.execute('INSERT OR IGNORE INTO corpus VALUES (?,?)',(config.digest,time.time()))
    def begin(self,config,metadata=None):
        ident=uuid.uuid4().hex
        atomic_write(self.root/'runs'/ident/'config.json',config.canonical())
        atomic_write(self.root/'runs'/ident/'metadata.json',json.dumps(metadata or {}))
        with self.db: self.db.execute('INSERT INTO runs VALUES (?,?,NULL,?,?,NULL)',(ident,time.time(),config.digest,'running'))
        return ident
    def finish(self,ident,config,result):
        namespace=result.get('namespace',''); pcs={str(x) for x in result.get('pcs',[])}
        if pcs and not namespace: raise ValueError('coverage namespace required')
        new=0
        with self.db:
            row=self.db.execute('SELECT digest,status FROM runs WHERE id=?',(ident,)).fetchone()
            if not row or row['status']!='running' or row['digest']!=config.digest: raise ValueError('run state/config mismatch')
            if result.get('coverage_valid') and not result.get('saturated') and namespace:
                for pc in pcs: new+=self.db.execute('INSERT OR IGNORE INTO coverage VALUES (?,?)',(namespace,pc)).rowcount
                if new:
                    atomic_write(self.root/'corpus'/(config.digest+'.json'),config.canonical())
                    self.db.execute('INSERT OR IGNORE INTO corpus VALUES (?,?)',(config.digest,time.time()))
            result['new_pcs']=new; result['corpus_added']=bool(new)
            for error in result.get('errors',[]):
                sig=hashlib.sha256((error['kind']+':'+error['summary']).encode()).hexdigest()
                self.db.execute('''INSERT INTO errors VALUES (?,?,?,?,1) ON CONFLICT(signature)
                    DO UPDATE SET last_run=excluded.last_run,occurrences=errors.occurrences+1''',
                    (sig,error['kind'],ident,ident))
            atomic_write(self.root/'runs'/ident/'result.json',json.dumps(result,indent=2))
            self.db.execute('UPDATE runs SET finished=?,status=?,result=? WHERE id=?',
                (time.time(),'error' if result.get('errors') else 'ok',json.dumps(result),ident))
        return new
    def corpus(self): return [self.root/'corpus'/(r[0]+'.json') for r in self.db.execute('SELECT digest FROM corpus ORDER BY digest')]
    def recover(self):
        with self.db: self.db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'",(time.time(),))
    def close(self): self.db.close()
