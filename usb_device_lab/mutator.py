import copy
import random
import zlib
from .model import DeviceConfig
from .mutation_graph import targets,edit,path_name,digest

class Mutator:
    VERSION='usb-tree-model-valid-v2'
    def __init__(self,seed=0):self.random=random.Random(seed)
    def catalogue(self,parent):
        return dict(version=self.VERSION,targets=[dict(path=path_name(p),kind=k) for p,k in targets(parent.data)],
            excluded=dict(schema_version='Storage schema',udc_driver='Local controller binding',udc_device='Local controller binding',
                          metadata='Research metadata',protocol_name='Executor plugin selector'))
    def mutate(self,parent,steps=None,target=None):
        count=self.random.randint(1,4) if steps is None else steps
        if type(count) is not int or not 1<=count<=16:raise ValueError('steps must be 1..16')
        data=copy.deepcopy(parent.data);trace=[]
        for _ in range(count):
            choices=targets(data)
            if target is not None:choices=[entry for entry in choices if entry[0]==tuple(target)]
            if not choices:raise ValueError('No eligible mutation targets')
            path,_=self.random.choice(choices);accepted=False;last_error='No effective change'
            for retry in range(16):
                candidate=copy.deepcopy(data)
                try:
                    entry=edit(candidate,path,self.random)
                    if not entry['changed']:continue
                    checked=DeviceConfig(candidate)
                except (ValueError,KeyError,TypeError,OverflowError,IndexError) as error:
                    last_error=type(error).__name__+': '+str(error)[:256];continue
                data=checked.data;entry.update(status='accepted',engine=self.VERSION,validation='model_only_not_UDC_or_class_conformance')
                trace.append(entry);accepted=True;break
            if not accepted:trace.append(dict(path=path_name(path),status='skipped',reason=last_error,engine=self.VERSION))
        wire=data.setdefault('wire_mutator',{})
        if target is None or tuple(target)!=('wire_mutator','seed'):
            before=wire.get('seed',0);seed=self.random.getrandbits(64);wire['seed']=seed
            trace.append(dict(path='/wire_mutator/seed',operation='wire_seed',before_sha256=digest(before),after_sha256=digest(seed),seed=seed,status='accepted',engine=self.VERSION))
        wire.setdefault('probability',0.05)
        return DeviceConfig(data),trace

class WireMutator:
    def __init__(self,options=None):
        options=options or {}; self.seed=int(options.get('seed',0)); self.probability=float(options.get('probability',0)); self.streams={}
        if not 0<=self.probability<=1: raise ValueError('invalid probability')
    def apply(self,channel,payload,maximum=65535):
        if payload is None: return None
        if len(payload)>maximum: raise ValueError('response too large')
        if channel not in self.streams: self.streams[channel]=random.Random(self.seed^zlib.crc32(channel.encode()))
        rng=self.streams[channel]
        if rng.random()<self.probability:
            data=bytearray(payload)
            op=rng.randrange(3); p=rng.randrange(len(data)) if data else 0
            if op==0 and data: data[p]^=1<<rng.randrange(8)
            elif op==1 and data: del data[p]
            elif len(data)<maximum: data[p:p]=bytes([rng.randrange(256)])
            return bytes(data)
        return bytes(payload)
