import copy
import random
import zlib
from .model import DeviceConfig

class Mutator:
    def __init__(self,seed=0): self.random=random.Random(seed)
    def mutate(self,parent,steps=None):
        d=copy.deepcopy(parent.data); rng=self.random; trace=[]
        candidates=[(x,'hex') for x in d['descriptors']]
        candidates.extend((x,'reply_hex') for x in d.get('protocol',{}).get('rules',[]) if 'reply_hex' in x)
        for _ in range(steps if steps is not None else rng.randint(1,4)):
            obj,key=rng.choice(candidates); data=bytearray.fromhex(obj[key]); p=rng.randrange(len(data)) if data else 0
            op=rng.choice(('bit','byte','insert','delete','truncate','word'))
            if op=='bit' and data: data[p]^=1<<rng.randrange(8)
            elif op=='byte' and data: data[p]=rng.choice((0,1,7,9,18,127,128,255))
            elif op=='insert' and len(data)<65535: data[p:p]=bytes([rng.randrange(256)])
            elif op=='delete' and data: del data[p:p+rng.randint(1,8)]
            elif op=='truncate' and data: del data[p:]
            elif op=='word' and len(data)>1:
                p=min(p,len(data)-2); data[p:p+2]=rng.choice((0,1,64,255,256,1024,65535)).to_bytes(2,'little')
            obj[key]=data.hex(); trace.append({'operation':op,'offset':p,'field':key})
        seed=rng.getrandbits(64); d['wire_mutator']={'seed':seed,'probability':0.05}
        trace.append({'operation':'wire_seed','seed':seed})
        return DeviceConfig(d),trace

class WireMutator:
    def __init__(self,options=None):
        options=options or {}; self.seed=int(options.get('seed',0)); self.probability=float(options.get('probability',0)); self.streams={}
        if not 0<=self.probability<=1: raise ValueError('invalid probability')
    def apply(self,channel,payload,maximum=65535):
        if payload is None: return None
        data=bytearray(payload)
        if len(data)>maximum: raise ValueError('response too large')
        if channel not in self.streams: self.streams[channel]=random.Random(self.seed^zlib.crc32(channel.encode()))
        rng=self.streams[channel]
        if rng.random()<self.probability:
            op=rng.randrange(3); p=rng.randrange(len(data)) if data else 0
            if op==0 and data: data[p]^=1<<rng.randrange(8)
            elif op==1 and data: del data[p]
            elif len(data)<maximum: data[p:p]=bytes([rng.randrange(256)])
        return bytes(data)
