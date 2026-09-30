import copy

def integer(x,low,high):
    if type(x) is not int or not low<=x<=high: raise ValueError(f'integer {low}..{high} required')
    return x

def validate_topology(data):
    r=data.get('runtime',{})
    configs=copy.deepcopy(r.get('configurations',[{'value':r.get('configuration_value',1),'interfaces':[
        {'number':0,'alternates':[{'setting':0,'endpoints':r.get('endpoints',[])}]}]}]))
    if not isinstance(configs,list) or not 1<=len(configs)<=255: raise ValueError('invalid configurations')
    values=set()
    for c in configs:
        value=integer(c['value'],1,255)
        if value in values: raise ValueError('duplicate configuration')
        values.add(value); numbers=set()
        if len(c['interfaces'])>256: raise ValueError('too many interfaces')
        for i in c['interfaces']:
            number=integer(i['number'],0,255)
            if number in numbers: raise ValueError('duplicate interface')
            numbers.add(number); settings=set()
            if not 1<=len(i['alternates'])<=256: raise ValueError('invalid alternates')
            for a in i['alternates']:
                setting=integer(a['setting'],0,255)
                if setting in settings: raise ValueError('duplicate alternate')
                settings.add(setting); addresses=set()
                if len(a.get('endpoints',[]))>30: raise ValueError('too many endpoints')
                for e in a.get('endpoints',[]):
                    b=bytes.fromhex(e['descriptor_hex'])
                    if len(b)!=7 or b[:2]!=b'\x07\x05' or b[3]&3 not in (2,3): raise ValueError('bulk/interrupt runtime endpoint required')
                    address=b[2]
                    if address&0x70 or not address&15 or address in addresses: raise ValueError('invalid/duplicate endpoint')
                    addresses.add(address); mps=int.from_bytes(b[4:6],'little')
                    integer(mps,1,64 if data.get('speed',2)==2 else 1024)
                    if data.get('speed',2)==3 and b[3]&3==2 and mps!=512: raise ValueError('high speed bulk requires 512')
                    if len(bytes.fromhex(e.get('payload_hex','')))>16384: raise ValueError('payload too large')
                    integer(e.get('interval_ms',10),1,10000)
                    integer(e.get('read_length',16384),1,16384)
            if 0 not in settings: raise ValueError('alternate zero required')
    return {c['value']:c for c in configs}

def active_endpoints(config,settings):
    result=[]
    for i in config['interfaces']:
        alt=next(a for a in i['alternates'] if a['setting']==settings[i['number']])
        result.extend(copy.deepcopy(alt.get('endpoints',[])))
    addresses=[bytes.fromhex(e['descriptor_hex'])[2] for e in result]
    if len(result)>30 or len(set(addresses))!=len(addresses): raise ValueError('active endpoint collision/limit')
    return result
