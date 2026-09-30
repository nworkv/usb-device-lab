import json
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

PAGE="""<!doctype html><meta charset="utf-8"><title>USB Device Lab WIP</title>
<style>body{font:15px monospace;margin:2em;background:#101821;color:#ddd}button{margin:.4em}pre{white-space:pre-wrap}td,th{padding:.4em;border:1px solid #555}table{border-collapse:collapse}a{color:#6cf}</style>
<h1>USB Device Lab WIP</h1><p>Read-only local dashboard</p>
<select id="filter"><option value="">All runs</option><option>error</option><option>ok</option><option>interrupted</option><option>running</option></select>
<button id="reload">Refresh</button><button id="prev">Previous</button><button id="next">Next</button>
<table><thead><tr><th>Run</th><th>Status</th><th>Started</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<h2>Run details</h2><div id="download"></div><pre id="details"></pre>
<h2>Error groups</h2><pre id="errors"></pre>
<script>
let offset=0;
async function get(url){const r=await fetch(url);if(!r.ok)throw new Error(url+' '+r.status);return r.json()}
async function refresh(){
 const q=new URLSearchParams({offset,status:document.querySelector('#filter').value});
 const runs=await get('/api/runs?'+q),rows=document.querySelector('#rows');rows.replaceChildren();
 for(const x of runs){const tr=document.createElement('tr');
  for(const v of [x.id,x.status,new Date(x.started*1000).toISOString()]){const td=document.createElement('td');td.textContent=v;tr.append(td)}
  const td=document.createElement('td'),b=document.createElement('button');b.textContent='Open';b.onclick=()=>openRun(x.id);td.append(b);tr.append(td);rows.append(tr)}
 document.querySelector('#errors').textContent=JSON.stringify(await get('/api/errors'),null,2);
}
async function openRun(id){
 document.querySelector('#details').textContent=JSON.stringify(await get('/api/run/'+id),null,2);
 const a=document.createElement('a');a.href='/api/config/'+id;a.textContent='Download configuration';a.download='config.json';
 document.querySelector('#download').replaceChildren(a);
}
document.querySelector('#reload').onclick=refresh;
document.querySelector('#filter').onchange=()=>{offset=0;refresh()};
document.querySelector('#prev').onclick=()=>{offset=Math.max(0,offset-50);refresh()};
document.querySelector('#next').onclick=()=>{offset+=50;refresh()};
refresh();
</script>"""
CSP="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'"

def make_server(directory,port=8080):
    root=Path(directory).resolve();database=root/'runs.sqlite3'
    if not database.exists(): raise ValueError('campaign database not found')
    class Handler(BaseHTTPRequestHandler):
        def allowed_host(self):
            host=(self.headers.get('Host') or '').rsplit(':',1)[0].strip('[]')
            return host in ('127.0.0.1','localhost','::1')
        def do_GET(self):
            if not self.allowed_host(): return self.send_error(403)
            url=urlsplit(self.path)
            try:
                with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
                    db.row_factory=sqlite3.Row
                    if url.path=='/': return self.reply(PAGE.encode(),'text/html; charset=utf-8')
                    if url.path=='/api/runs':
                        q=parse_qs(url.query);offset=max(0,min(int(q.get('offset',['0'])[0]),10000000))
                        status=q.get('status',[''])[0]
                        rows=db.execute('SELECT id,status,started,digest FROM runs WHERE (?="" OR status=?) ORDER BY started DESC LIMIT 50 OFFSET ?',(status,status,offset))
                        return self.data([dict(x) for x in rows])
                    if url.path=='/api/errors':
                        return self.data([dict(x) for x in db.execute('SELECT * FROM errors ORDER BY occurrences DESC LIMIT 200')])
                    m=re.fullmatch(r'/api/(run|config)/([0-9a-f]{32})',url.path)
                    if m:
                        kind,ident=m.groups();row=db.execute('SELECT * FROM runs WHERE id=?',(ident,)).fetchone()
                        if row:
                            if kind=='config': return self.reply((root/'runs'/ident/'config.json').read_bytes(),'application/json')
                            item=dict(row)
                            item['result']=json.loads(item['result']) if item['result'] else None
                            meta=root/'runs'/ident/'metadata.json'
                            item['metadata']=json.loads(meta.read_text()) if meta.exists() else None
                            return self.data(item)
                self.send_error(404)
            except (ValueError,OSError,sqlite3.Error): self.send_error(400)
        def data(self,value): self.reply(json.dumps(value).encode(),'application/json')
        def reply(self,body,content_type):
            self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',CSP);self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def serve(directory,port=8080):
    server=make_server(directory,port)
    try: server.serve_forever()
    finally: server.server_close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='USB Device Lab read-only dashboard')
    p.add_argument('--output',default='state');p.add_argument('--port',type=int,default=8080);a=p.parse_args()
    serve(a.output,a.port)
