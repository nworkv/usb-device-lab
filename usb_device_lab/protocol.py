import importlib
import os
from collections import defaultdict,deque

class Protocol:
    def __init__(self,options=None): self.options=options or {}; self.reset()
    def reset(self): self.queues=defaultdict(deque); self.queued=0
    def handles(self,setup): return False
    def control(self,setup,data): return b''
    def out(self,address,data): pass
    def incoming(self,address):
        if not self.queues[address]: return None
        data=self.queues[address].popleft(); self.queued-=len(data); return data
    def read_size(self,address): return self.options.get('read_sizes',{}).get(str(address),16384)
    def configured(self,value): self.reset()
    def alternate(self,interface,setting): pass
    def clear_halt(self,address): pass
    def close(self): pass
    def send(self,address,data):
        if len(data)>16384 or self.queued+len(data)>1048576 or len(self.queues[address])>=256: raise ValueError('queue limit')
        self.queues[address].append(bytes(data)); self.queued+=len(data)

class Script(Protocol):
    def reset(self):
        super().reset(); self.state=self.options.get('initial_state','start'); self.variables={}
    def rule(self,event,setup=None,address=None,data=None):
        for r in self.options.get('rules',[]):
            if r.get('event')!=event or r.get('state','*') not in ('*',self.state): continue
            if address is not None and r.get('address',address)!=address: continue
            if setup is not None and any(setup.get(k)!=v for k,v in r.get('match',{}).items() if v!=-1): continue
            if data is not None and not data.startswith(bytes.fromhex(r.get('prefix',''))): continue
            return r
        return None
    def handles(self,setup): return setup['wLength']<=16384 and self.rule('control',setup=setup) is not None
    def perform(self,r,data):
        if 'capture' in r:
            if len(data)>16384 or len(self.variables)>=64 and r['capture'] not in self.variables: raise ValueError('capture limit')
            self.variables[r['capture']]=bytes(data)
        reply=self.variables.get(r['reply_var'],b'') if 'reply_var' in r else bytes.fromhex(r.get('reply_hex',''))
        if r.get('echo'): reply=bytes(data)
        if len(reply)>16384: raise ValueError('reply too large')
        for s in r.get('send',[]): self.send(s['address'],self.variables.get(s['var'],b'') if 'var' in s else bytes.fromhex(s.get('hex','')))
        self.state=r.get('next_state',self.state)
        return reply
    def control(self,setup,data):
        r=self.rule('control',setup=setup,data=data)
        if r is None: raise ValueError('unmatched control data')
        return self.perform(r,data)
    def out(self,address,data):
        r=self.rule('out',address=address,data=data)
        if r is not None: self.perform(r,data)
    def incoming(self,address):
        queued=super().incoming(address)
        if queued is not None: return queued
        r=self.rule('in',address=address)
        return self.perform(r,b'') if r is not None else None

def load_protocol(options=None):
    options=options or {}; name=options.get('name','script')
    if name=='script': return Script(options)
    if name not in os.environ.get('USB_DEVICE_LAB_PLUGINS','').split(',') or ':' not in name: raise ValueError('plugin not allowlisted')
    module,cls=name.split(':',1); result=getattr(importlib.import_module(module),cls)(options)
    if not isinstance(result,Protocol): raise ValueError('plugin must inherit Protocol')
    return result
