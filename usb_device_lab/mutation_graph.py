"""Bounded USB-tree operators; model validity is not UDC/class conformance."""
import copy
import hashlib
import json
from .usb_speeds import SUPPORTED_MODEL_SPEEDS

ROOTS=('speed','descriptors','runtime','protocol','wire_mutator')
HEX={'hex','reply_hex','payload_hex','descriptor_hex','prefix'}
LOCKED={('protocol','name'):'Executor plugin selector, not USB device data'}
MAX_TARGETS=16384

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def walk(value,path=()):
    if len(path)>32:raise ValueError('USB mutation tree exceeds depth 32')
    if path in LOCKED:return
    if isinstance(value,dict):
        for key in sorted(value):yield from walk(value[key],path+(key,))
    elif isinstance(value,list):
        yield path,'list'
        for i,item in enumerate(value):yield from walk(item,path+(i,))
    elif type(value) in (str,int,float,bool):yield path,'scalar'

def targets(data):
    result=[]
    for name in ROOTS:
        if name in data:
            for entry in walk(data[name],(name,)):
                if len(result)>=MAX_TARGETS:raise ValueError('USB mutation tree exceeds target limit')
                result.append(entry)
    return result

def at(data,path):
    for key in path:data=data[key]
    return data

def assign(data,path,value):
    node=data
    for key in path[:-1]:node=node[key]
    node[path[-1]]=value

def path_name(path):return '/'+ '/'.join(str(k).replace('~','~0').replace('/','~1') for k in path)

def runtime_descriptors(data):
    for path,kind in walk(data.get('runtime',{}),('runtime',)):
        if kind=='scalar' and path[-1]=='descriptor_hex':
            value=bytearray.fromhex(at(data,path))
            if len(value)!=7:raise ValueError('Runtime endpoint must have seven bytes')
            yield path,value

def speed_choices(data):
    interrupt_only=all(value[3]&3==3 for _,value in runtime_descriptors(data))
    return tuple(speed for speed in SUPPORTED_MODEL_SPEEDS if speed!=1 or interrupt_only)

def byte_edit(text,rng):
    value=bytearray.fromhex(text);op=rng.choice(('bit','byte','insert','delete','truncate','word'))
    p=rng.randrange(len(value)) if value else 0
    if op=='bit' and value:value[p]^=1<<rng.randrange(8)
    elif op=='byte' and value:value[p]=rng.choice((0,1,7,9,18,127,128,255))
    elif op=='insert' and len(value)<65535:value[p:p]=bytes([rng.randrange(256)])
    elif op=='delete' and value:del value[p:p+rng.randint(1,8)]
    elif op=='truncate' and value:del value[p:]
    elif op=='word' and len(value)>1:
        p=min(p,len(value)-2);value[p:p+2]=rng.choice((0,1,64,255,256,1024,65535)).to_bytes(2,'little')
    return value.hex(),op,p

def edit(data,path,rng):
    old=at(data,path);key=path[-1];dependencies=[]
    if key=='probability':
        value=rng.choice((0.0,0.01,0.05,0.2,0.5,1.0));op='probability';offset=None
    elif type(old) is str and key=='descriptor_hex' and path[0]=='runtime':
        b=bytearray.fromhex(old)
        if len(b)!=7:raise ValueError('Runtime endpoint must have seven bytes')
        speed=data.get('speed',2)
        field=rng.choice(('address','type','packet_size','interval'))
        if field=='address':b[2]=rng.choice((0,128))|rng.randint(1,15)
        elif field=='type':b[3]=(b[3]&252)|rng.choice((3,) if speed==1 else (2,3))
        elif field=='packet_size':
            high=speed==3
            sizes=(1,2,4,8) if speed==1 else (1,8,16,32,64,1024) if high else (1,8,16,32,64)
            size=512 if high and b[3]&3==2 else rng.choice(sizes)
            b[4:6]=size.to_bytes(2,'little')
        else:b[6]=rng.choice((10,16,32,64,255) if speed==1 else (0,1,2,4,8,10,16,255))
        value=b.hex();op='runtime_endpoint_'+field;offset=None
    elif type(old) is str and key in HEX:value,op,offset=byte_edit(old,rng)
    elif type(old) is bool:value=not old;op='boolean';offset=None
    elif type(old) is int:
        choices=(0,1,2,3,7,8,15,16,31,32,63,64,127,128,255,256,511,512,1023,1024,65535)
        if path==('speed',):choices=speed_choices(data)
        elif key=='interval_ms':choices=(1,2,5,10,100,1000,10000)
        elif key=='read_length':choices=(1,8,31,64,512,4096,16384)
        elif key=='seed':choices=(0,1,rng.getrandbits(64))
        elif key in ('bmRequestType','bRequest','type','index','address','number','setting','value'):choices=tuple(v for v in choices if v<=255)
        value=rng.choice(choices);op='integer_boundary';offset=None
    elif type(old) is float:
        value=rng.choice((0.0,0.01,0.05,0.2,0.5,1.0));op='float_boundary';offset=None
    elif type(old) is str:
        if key=='event':value=rng.choice(('control','in','out'))
        elif key in ('capture','reply_var','var','state','next_state','initial_state'):value=rng.choice(('start','state0','state1','rx','value0'))
        else:value=(old[:63]+'_m')[:64]
        op='string';offset=None
    elif isinstance(old,list):
        value=copy.deepcopy(old);op=rng.choice(('list_duplicate','list_remove','list_swap'));offset=None
        if op=='list_duplicate' and value and len(value)<256:value.insert(rng.randrange(len(value)+1),copy.deepcopy(rng.choice(value)))
        elif op=='list_remove' and value:del value[rng.randrange(len(value))]
        elif op=='list_swap' and len(value)>1:
            a,b=rng.sample(range(len(value)),2);value[a],value[b]=value[b],value[a]
    else:raise ValueError('Unsupported mutation target')
    assign(data,path,value)
    if path==('speed',) and old!=value:
        for child,b in list(runtime_descriptors(data)):
            text=at(data,child);size=int.from_bytes(b[4:6],'little')
            adjusted=512 if value==3 and b[3]&3==2 else min(size,64) if value==2 else min(size,8) if value==1 else size
            b[4:6]=adjusted.to_bytes(2,'little')
            if value==1:b[6]=max(10,b[6])
            replacement=b.hex()
            if replacement!=text:
                assign(data,child,replacement)
                dependencies.append(dict(path=path_name(child),before_sha256=digest(text),after_sha256=digest(replacement)))
    return dict(path=path_name(path),operation=op,offset=offset,before_sha256=digest(old),after_sha256=digest(value),changed=old!=value,dependent_changes=dependencies)
