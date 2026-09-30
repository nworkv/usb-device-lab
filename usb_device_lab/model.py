import copy
import hashlib
import json
from pathlib import Path
from .topology import validate_topology

def blob(value):
    if not isinstance(value,str): raise ValueError('hex string required')
    data=bytes.fromhex(value)
    if len(data)>65535: raise ValueError('descriptor exceeds 65535 bytes')
    return data

class DeviceConfig:
    def __init__(self,data):
        self.data=copy.deepcopy(data)
        self.validate()
    @classmethod
    def load(cls,path): return cls(json.loads(Path(path).read_text(encoding='utf-8')))
    def validate(self):
        d=self.data
        if not isinstance(d,dict) or d.get('schema_version')!=1: raise ValueError('schema_version must be 1')
        for name in ('udc_driver','udc_device'):
            s=d.get(name,'')
            if not isinstance(s,str) or not s or '\0' in s or len(s.encode())>=128: raise ValueError('invalid '+name)
        if type(d.get('speed',2)) is not int or d.get('speed',2) not in (2,3): raise ValueError('full/high speed only')
        entries=d.get('descriptors',[])
        if not isinstance(entries,list) or not 1<=len(entries)<=256: raise ValueError('1..256 descriptors required')
        keys=set()
        for x in entries:
            key=tuple(x.get(k,0) for k in ('type','index','wIndex'))
            if any(type(v) is not int or not 0<=v<=limit for v,limit in zip(key,(255,255,65535))): raise ValueError('invalid descriptor selector')
            if key in keys: raise ValueError('duplicate descriptor selector')
            keys.add(key); blob(x['hex'])
        if not {(1,0,0),(2,0,0)}<=keys: raise ValueError('device and configuration required')
        validate_topology(d)
        options=d.get('protocol',{})
        if not isinstance(options,dict) or len(options.get('rules',[]))>256: raise ValueError('invalid protocol')
        wire=d.get('wire_mutator',{})
        probability=wire.get('probability',0)
        if not isinstance(probability,(int,float)) or not 0<=probability<=1: raise ValueError('invalid wire probability')
    def canonical(self): return json.dumps(self.data,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    @property
    def digest(self): return hashlib.sha256(self.canonical().encode()).hexdigest()
    def save(self,path): Path(path).write_text(json.dumps(self.data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
