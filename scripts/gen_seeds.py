#!/usr/bin/env python3
"""Generate corpus/seeds/*.json from compact profile definitions.

Run from the repository root: python3 scripts/gen_seeds.py
Every generated file is validated with DeviceConfig before it is written.
Isochronous endpoints (Audio/UVC streaming) are declared in descriptors only:
the Raw Gadget backend executes bulk/interrupt endpoints, so streaming
alternate settings are enumerated by the host but carry no runtime traffic.
"""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from usb_device_lab.model import DeviceConfig  # noqa: E402

def h(*parts): return b''.join(bytes(p) for p in parts)
def le16(v): return [v&255,v>>8]
def device(cls=0,sub=0,proto=0,vid=0x1d6b,pid=0x0104,bcd=0x0100,configs=1,mps=64):
    return h([18,1],le16(0x0200),[cls,sub,proto,mps],le16(vid),le16(pid),le16(bcd),[1,2,3,configs])
def iface(num,alt,neps,cls,sub=0,proto=0,s=0): return h([9,4,num,alt,neps,cls,sub,proto,s])
def ep(addr,attr,mps,interval): return h([7,5,addr,attr],le16(mps),[interval])
def iad(first,count,cls,sub=0,proto=0): return h([8,11,first,count,cls,sub,proto,0])
def config(value,ninterfaces,body,attrs=0x80,power=50):
    return h([9,2],le16(9+len(body)),[ninterfaces,value,0,attrs,power])+body
def string(text): data=text.encode('utf-16-le'); return h([2+len(data),3])+data
def strings(product):
    return [{'type':3,'index':0,'hex':'04030904'}]+[{'type':3,'index':i,'hex':string(t).hex()} for i,t in ((1,'USB Device Lab'),(2,product),(3,'0001'))]
def rep(addr,attr,mps,interval,**extra): return dict({'descriptor_hex':ep(addr,attr,mps,interval).hex()},**extra)
def alt(setting,endpoints=()): return {'setting':setting,'endpoints':list(endpoints)}
def itf(number,*alternates): return {'number':number,'alternates':list(alternates)}
def wrap(desc,runtime,rules,configs=None):
    return {'descriptors':desc,'runtime':{'configurations':configs or [{'value':1,'interfaces':runtime}]},'protocol':{'name':'script','rules':rules}}

KEYBOARD=bytes.fromhex('05010906a101050719e029e71500250175019508810295017508810195057501050819012905910295017503910195067508150025650507190029658100c0')
MOUSE=bytes.fromhex('05010902a1010901a10005091901290315002501950375018102950175058101050109300931093815817f750895038106c0c0')
CONSUMER=bytes.fromhex('050c0901a1011500250109e909ea09e209cd09b509b6750195068102950275018101c0')
GAMEPAD=bytes.fromhex('05010905a1011500250105091901291075019510810205010930093109320935150026ff007508950481020939150025073500463b016514750495018142c0')
MULTI=bytes.fromhex('05010906a10185010507190029651500256575089506810005010902a10185020901a10005091901290315002501950375018102950175058101050109300931150025ff750895028106c0c0050c0901a10185031500250109e909ea750195028102950675018101c0')

def hid_descriptor(report): return h([9,0x21],le16(0x0111),[0,1,0x22],le16(len(report)))

def hid(product,report,sub,proto,mps,payload,out=False):
    eps=[ep(0x81,3,mps,10)]+([ep(0x01,3,mps,10)] if out else [])
    body=iface(0,0,len(eps),3,sub,proto)+hid_descriptor(report)+b''.join(eps)
    runtime=[rep(0x81,3,mps,10,payload_hex=payload.hex())]+([rep(0x01,3,mps,10,read_length=mps)] if out else [])
    desc=[{'type':1,'hex':device(pid=0x0110).hex()},{'type':2,'hex':config(1,1,body).hex()},{'type':0x22,'wIndex':0,'hex':report.hex()}]+strings(product)
    rules=[{'event':'control','match':{'bmRequestType':0xa1,'bRequest':1},'reply_hex':payload.hex()},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':9},'capture':'output_report'},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':10}},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':11}},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':2},'reply_hex':'00'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':3},'reply_hex':'01'}]
    return wrap(desc,[itf(0,alt(0,runtime))],rules)

def cdc_acm_parts(first=0,base=1):
    n,d=first,first+1
    func=h([5,0x24,0],le16(0x0110))+h([5,0x24,1,0,d])+h([4,0x24,2,2])+h([5,0x24,6,n,d])
    body=iad(n,2,2,2,1)+iface(n,0,1,2,2,1)+func+ep(0x80|(base+1),3,16,16)+iface(d,0,2,10)+ep(0x80|base,2,64,0)+ep(base,2,64,0)
    runtime=[itf(n,alt(0,[rep(0x80|(base+1),3,16,16,payload_hex='a1200000000002000300',interval_ms=100)])),
             itf(d,alt(0,[rep(0x80|base,2,64,0),rep(base,2,64,0,read_length=64)]))]
    rules=[{'event':'control','match':{'bmRequestType':0x21,'bRequest':0x20},'capture':'line_coding'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':0x21},'reply_var':'line_coding'},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':0x22}},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':0x23}},
           {'event':'out','address':base,'capture':'rx','send':[{'address':0x80|base,'var':'rx'}]}]
    return body,runtime,rules

def cdc_acm():
    body,rt,rules=cdc_acm_parts()
    return wrap([{'type':1,'hex':device(0xef,2,1,pid=0x0121).hex()},{'type':2,'hex':config(1,2,body).hex()}]+strings('CDC ACM'),rt,rules)

def cdc_ecm():
    func=h([5,0x24,0],le16(0x0110))+h([5,0x24,6,0,1])+h([13,0x24,0x0f,4],[0,0,0,0],le16(1514),le16(0),[0])
    body=iface(0,0,1,2,6,0)+func+ep(0x82,3,16,32)+iface(1,0,0,10)+iface(1,1,2,10)+ep(0x81,2,64,0)+ep(0x01,2,64,0)
    rt=[itf(0,alt(0,[rep(0x82,3,16,32,payload_hex='a100010000000000',interval_ms=200)])),
        itf(1,alt(0),alt(1,[rep(0x81,2,64,0),rep(0x01,2,64,0,read_length=64)]))]
    desc=[{'type':1,'hex':device(2,0,0,pid=0x0120).hex()},{'type':2,'hex':config(1,2,body).hex()}]+strings('CDC ECM')
    desc.append({'type':3,'index':4,'hex':string('020000000001').hex()})
    return wrap(desc,rt,[{'event':'control','match':{'bmRequestType':0x21,'bRequest':0x43}},{'event':'out','address':1,'capture':'frame'}])

def as_interface(num,addr,link):
    return (iface(num,0,0,1,2,0)+iface(num,1,1,1,2,0)+h([7,0x24,1,link,1],le16(1))
            +h([11,0x24,2,1,2,2,16,1],[0x80,0xbb,0])+h([9,5,addr,0x0d],le16(192),[1,0,0])+h([7,0x25,1,1,0],le16(0)))

def uac1(direction):
    streams=[]
    if direction in ('playback','duplex'): streams.append(0x01)
    if direction in ('capture','duplex'): streams.append(0x82)
    terms=b'';links=[];tid=1
    for addr in streams:
        if addr&0x80:
            terms+=h([12,0x24,2,tid],le16(0x0201),[0,2],le16(3),[0,0])+h([9,0x24,3,tid+1],le16(0x0101),[0,tid,0]);links.append(tid+1)
        else:
            terms+=h([12,0x24,2,tid],le16(0x0101),[0,2],le16(3),[0,0])+h([9,0x24,3,tid+1],le16(0x0301),[0,tid,0]);links.append(tid)
        tid+=2
    nums=list(range(1,len(streams)+1))
    header=h([8+len(nums),0x24,1],le16(0x0100),le16(8+len(nums)+len(terms)),[len(nums)],nums)
    body=iface(0,0,0,1,1,0)+header+terms;rt=[itf(0,alt(0))]
    for num,addr,link in zip(nums,streams,links):
        body+=as_interface(num,addr,link);rt.append(itf(num,alt(0),alt(1)))
    desc=[{'type':1,'hex':device(pid=0x0130).hex()},{'type':2,'hex':config(1,1+len(streams),body).hex()}]+strings('UAC1 '+direction)
    rules=[{'event':'control','match':{'bmRequestType':0x22,'bRequest':1},'capture':'rate'},
           {'event':'control','match':{'bmRequestType':0xa2,'bRequest':0x81},'reply_hex':'80bb00'},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':1},'capture':'feature'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':0x81},'reply_hex':'0000'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':0x82},'reply_hex':'00c0'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':0x83},'reply_hex':'0000'},
           {'event':'control','match':{'bmRequestType':0xa1,'bRequest':0x84},'reply_hex':'0001'}]
    return wrap(desc,rt,rules)

CSW='55534253000000000000000000'
def storage():
    body=iface(0,0,2,8,6,0x50)+ep(0x81,2,64,0)+ep(0x02,2,64,0)
    rt=[itf(0,alt(0,[rep(0x81,2,64,0),rep(0x02,2,64,0,read_length=31)]))]
    rules=[{'event':'control','match':{'bmRequestType':0xa1,'bRequest':0xfe},'reply_hex':'00'},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':0xff}},
           {'event':'out','address':2,'prefix':'55534243','capture':'cbw','send':[{'address':0x81,'hex':CSW}]}]
    return wrap([{'type':1,'hex':device(pid=0x0140).hex()},{'type':2,'hex':config(1,1,body).hex()}]+strings('Bulk-only storage'),rt,rules)

def midi():
    ac=iface(0,0,0,1,1,0)+h([9,0x24,1],le16(0x0100),le16(9),[1,1])
    jacks=h([6,0x24,2,1,1,0])+h([6,0x24,2,2,2,0])+h([9,0x24,3,1,3,1,2,1,0])+h([9,0x24,3,2,4,1,1,1,0])
    eps=h([9,5,0x01,2],le16(64),[0,0,0])+h([5,0x25,1,1,1])+h([9,5,0x81,2],le16(64),[0,0,0])+h([5,0x25,1,1,3])
    ms=iface(1,0,2,1,3,0)+h([7,0x24,1],le16(0x0100),le16(7+len(jacks)+len(eps)))+jacks+eps
    rt=[itf(0,alt(0)),itf(1,alt(0,[rep(0x01,2,64,0,read_length=64),rep(0x81,2,64,0)]))]
    rules=[{'event':'out','address':1,'capture':'midi','send':[{'address':0x81,'var':'midi'}]}]
    return wrap([{'type':1,'hex':device(pid=0x0150).hex()},{'type':2,'hex':config(1,2,ac+ms).hex()}]+strings('MIDI streaming'),rt,rules)

def uvc():
    term=h([18,0x24,2,1],le16(0x0201),[0,0],le16(0),le16(0),le16(0),[3,0,0,0])
    out=h([9,0x24,3,2],le16(0x0101),[0,1,0])
    vc=iad(0,2,14,3,0)+iface(0,0,1,14,1,0)+h([13,0x24,1],le16(0x0100),le16(13+len(term)+len(out)),[0x80,0x8d,0x5b,0],[1,1])+term+out
    vc+=ep(0x83,3,16,8)+h([5,0x25,3],le16(16))
    fmt=h([11,0x24,6,1,1,0,1,0,0,0,0])
    frame=h([30,0x24,7,1,0],le16(160),le16(120),[0,0x58,0x02,0],[0,0x10,0x0e,0],[0,0x96,0,0],[0x15,0x16,0x05,0],[1],[0x15,0x16,0x05,0])
    vs=iface(1,0,0,14,2,0)+h([14,0x24,1,1],le16(14+len(fmt)+len(frame)),[0x81,0,2,0,0,0,1,0])+fmt+frame
    vs+=iface(1,1,1,14,2,0)+h([7,5,0x81,5],le16(512),[1])
    probe='0100010115160500000000000000000000000000004b00000002000000'
    rt=[itf(0,alt(0,[rep(0x83,3,16,8,payload_hex='0201010000',interval_ms=500)])),itf(1,alt(0),alt(1))]
    rules=[{'event':'control','match':{'bmRequestType':0x21,'bRequest':1},'capture':'probe'}]
    rules+=[{'event':'control','match':{'bmRequestType':0xa1,'bRequest':r},'reply_hex':probe} for r in (0x81,0x82,0x83,0x87)]
    return wrap([{'type':1,'hex':device(0xef,2,1,pid=0x0160).hex()},{'type':2,'hex':config(1,2,vc+vs).hex()}]+strings('UVC streaming'),rt,rules)

def composite(kind):
    kb=iface(0,0,1,3,1,1)+hid_descriptor(KEYBOARD)+ep(0x81,3,8,10)
    hid_rt=itf(0,alt(0,[rep(0x81,3,8,10,payload_hex='0000000000000000')]))
    rules=[{'event':'control','match':{'bmRequestType':0x21,'bRequest':10}},
           {'event':'control','match':{'bmRequestType':0x21,'bRequest':9},'capture':'leds'}]
    if kind=='hid-cdc':
        body,rt,extra=cdc_acm_parts(1,2);body=kb+body;rt=[hid_rt]+rt;n=3
    elif kind=='hid-storage':
        body=kb+iface(1,0,2,8,6,0x50)+ep(0x82,2,64,0)+ep(0x02,2,64,0)
        rt=[hid_rt,itf(1,alt(0,[rep(0x82,2,64,0),rep(0x02,2,64,0,read_length=31)]))];n=2
        extra=[{'event':'control','match':{'bmRequestType':0xa1,'bRequest':0xfe},'reply_hex':'00'},
               {'event':'out','address':2,'prefix':'55534243','send':[{'address':0x82,'hex':CSW}]}]
    else:
        terms=h([12,0x24,2,1],le16(0x0101),[0,2],le16(3),[0,0])+h([9,0x24,3,2],le16(0x0301),[0,1,0])
        body=kb+iface(1,0,0,1,1,0)+h([9,0x24,1],le16(0x0100),le16(9+len(terms)),[1,2])+terms+as_interface(2,0x02,1)
        rt=[hid_rt,itf(1,alt(0)),itf(2,alt(0),alt(1))];n=3
        extra=[{'event':'control','match':{'bmRequestType':0x22,'bRequest':1},'capture':'rate'}]
    desc=[{'type':1,'hex':device(0xef,2,1,pid=0x0170).hex()},{'type':2,'hex':config(1,n,body).hex()},
          {'type':0x22,'wIndex':0,'hex':KEYBOARD.hex()}]+strings('Composite '+kind)
    return wrap(desc,rt,rules+extra)

def boundary(kind):
    if kind=='multi-config':
        b1=iface(0,0,1,0xff)+ep(0x81,3,8,10);b2=iface(0,0,2,0xff)+ep(0x81,2,64,0)+ep(0x01,2,64,0)
        desc=[{'type':1,'hex':device(pid=0x0180,configs=2).hex()},{'type':2,'hex':config(1,1,b1).hex()},{'type':2,'index':1,'hex':config(2,1,b2).hex()}]
        cfgs=[{'value':1,'interfaces':[itf(0,alt(0,[rep(0x81,3,8,10,payload_hex='00')]))]},
              {'value':2,'interfaces':[itf(0,alt(0,[rep(0x81,2,64,0),rep(0x01,2,64,0,read_length=64)]))]}]
        return wrap(desc+strings('Multi configuration'),None,[{'event':'out','address':1,'capture':'x','send':[{'address':0x81,'var':'x'}]}],cfgs)
    if kind=='multi-altsetting':
        alts=[alt(0)];body=iface(0,0,0,0xff)
        for s,mps in ((1,8),(2,16),(3,32),(4,64)):
            body+=iface(0,s,2,0xff)+ep(0x81,2,mps,0)+ep(0x01,2,mps,0)
            alts.append(alt(s,[rep(0x81,2,mps,0,payload_hex='ab'*mps),rep(0x01,2,mps,0,read_length=mps)]))
        desc=[{'type':1,'hex':device(pid=0x0181).hex()},{'type':2,'hex':config(1,1,body).hex()}]
        return wrap(desc+strings('Multi altsetting'),[itf(0,*alts)],[])
    eps=[]
    for n in range(1,8):
        bulk=n%2==1;mps=64 if bulk else 8;interval=0 if bulk else n
        eps+=[rep(0x80|n,2 if bulk else 3,mps,interval,payload_hex='%02x'%n),rep(n,2 if bulk else 3,mps,interval,read_length=8)]
    body=iface(0,0,len(eps),0xff)+b''.join(bytes.fromhex(e['descriptor_hex']) for e in eps)
    desc=[{'type':1,'hex':device(pid=0x0182).hex()},{'type':2,'hex':config(1,1,body).hex()}]
    return wrap(desc+strings('Endpoint matrix'),[itf(0,alt(0,eps))],[])

def profiles():
    yield 'hid','keyboard',hid('HID keyboard',KEYBOARD,1,1,8,bytes(8))
    yield 'hid','mouse',hid('HID mouse',MOUSE,1,2,4,bytes([0,1,1,0]))
    yield 'hid','consumer-control',hid('HID consumer control',CONSUMER,0,0,1,bytes(1))
    yield 'hid','gamepad',hid('HID gamepad',GAMEPAD,0,0,7,bytes([0x80,0x80,0x80,0x80,0x08,0,0]),out=True)
    yield 'hid','multi-report',hid('HID multi report',MULTI,0,0,8,bytes([1,0,0,0,0,0,0,0]))
    for d in ('playback','capture','duplex'): yield 'audio','uac1-'+d,uac1(d)
    yield 'cdc','acm',cdc_acm()
    yield 'cdc','ecm',cdc_ecm()
    yield 'storage','bulk-only',storage()
    yield 'midi','midi-streaming',midi()
    yield 'uvc','streaming',uvc()
    for k in ('hid-cdc','hid-audio','hid-storage'): yield 'composite',k,composite(k)
    for k in ('multi-config','multi-altsetting','endpoint-matrix'): yield 'boundary',k,boundary(k)

def main():
    out=ROOT/'corpus'/'seeds';out.mkdir(parents=True,exist_ok=True);count=0
    for family,profile,body in profiles():
        data={'schema_version':1,'udc_driver':'dummy_udc','udc_device':'dummy_udc.0','speed':2,'metadata':{'family':family,'profile':profile}}
        data.update(body)
        (out/f'{family}-{profile}.json').write_text(DeviceConfig(data).canonical()+'\n');count+=1
    print(f'{count} seeds written to {out}')

if __name__=='__main__': main()
