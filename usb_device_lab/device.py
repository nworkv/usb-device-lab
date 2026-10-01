import ctypes
import errno
import json
import signal
import struct
import sys
import threading
from .model import DeviceConfig
from .mutator import WireMutator
from .protocol import load_protocol
from .raw_io import (CLEAR_HALT,CONFIGURE,DISABLE,ENABLE,EP0_READ,EP0_WRITE,FETCH,INIT,READ,RUN,
                     SET_HALT,STALL,WRITE,RawIO)
from .topology import active_endpoints,validate_topology

JOIN_TIMEOUT=2

def log(**data): print(json.dumps(data),flush=True)

class Device:
    def __init__(self,config,io=None):
        self.data=config.data;self.configs=validate_topology(self.data);self.io=io;self.interrupts=False
        self.protocol=load_protocol(self.data.get('protocol'));self.wire=WireMutator(self.data.get('wire_mutator'))
        self.lock=threading.RLock();self.value=0;self.settings={};self.eps={};self.workers=[];self.halted=set()
        self.descriptors={(x['type'],x.get('index',0),x.get('wIndex',0)):bytes.fromhex(x['hex']) for x in self.data['descriptors']}
    def disable(self):
        """Stop endpoint workers without deadlocking.

        Order matters: (1) ask every worker to stop and interrupt blocking syscalls,
        (2) EP_DISABLE every endpoint so that Raw Gadget fails pending EP_READ/EP_WRITE
        with ESHUTDOWN, (3) only then join the threads. Joining before EP_DISABLE can
        hang forever on an endpoint the host never services.
        """
        workers,self.workers=self.workers,[]
        for _,stop,t in workers:
            stop.set()
            if t.is_alive() and self.interrupts:
                try: signal.pthread_kill(t.ident,signal.SIGUSR1)
                except (ProcessLookupError,OSError): pass
        failures=[]
        for handle,_,_ in workers:
            try: self.io.scalar(DISABLE,handle)
            except OSError as e:
                if e.errno not in (errno.EINVAL,errno.EBUSY,errno.ESHUTDOWN,errno.ENODEV): failures.append(e)
        stuck=[]
        for handle,_,t in workers:
            if t.ident is not None and t is not threading.current_thread(): t.join(timeout=JOIN_TIMEOUT)
            if t.is_alive() and t is not threading.current_thread(): stuck.append(handle)
        self.eps={};self.halted.clear();self.value=0;self.settings={}
        if stuck: raise RuntimeError(f'endpoint threads {stuck} did not stop after EP_DISABLE; restart executor')
        if failures: raise failures[0]
    def configure(self,value,settings=None):
        if value and value not in self.configs: raise ValueError('unknown configuration')
        selected=dict(settings) if settings is not None else ({i['number']:0 for i in self.configs[value]['interfaces']} if value else {})
        endpoints=active_endpoints(self.configs[value],selected) if value else []
        self.disable()
        if settings is None:
            with self.lock: self.protocol.configured(value)
        try:
            for ep in endpoints:
                raw=bytes.fromhex(ep['descriptor_hex']);handle=self.io.call(ENABLE,ctypes.create_string_buffer(raw+b'\0\0',9))
                stop=threading.Event();t=threading.Thread(target=self.worker,args=(handle,ep,stop),daemon=True)
                self.eps[raw[2]]=handle;self.workers.append((handle,stop,t))
            if value: self.io.call(CONFIGURE)
            self.value=value;self.settings=selected
            for _,_,t in self.workers: t.start()
        except Exception:
            self.disable();raise
    def send(self,request,data,length,channel,handle=0):
        with self.lock: data=self.wire.apply(channel,data,65535 if request==EP0_WRITE else 16384)
        self.io.transfer(request,data[:length],handle=handle)
    def control(self,raw):
        bm,req,v,i,n=struct.unpack('<BBHHH',raw)
        setup=dict(zip(('bmRequestType','bRequest','wValue','wIndex','wLength'),(bm,req,v,i,n)))
        log(kind='setup',**setup);channel=f'control:{bm}:{req}:{v}:{i}';reply=None
        try:
            if bm==0 and req==9 and not i and not n:
                self.configure(v);self.io.transfer(EP0_READ);return
            if bm==1 and req==11 and not n:
                if i not in self.settings: raise ValueError('unconfigured interface')
                iface=next(x for x in self.configs[self.value]['interfaces'] if x['number']==i)
                if v not in {a['setting'] for a in iface['alternates']}: raise ValueError('unknown alternate setting')
                selected=dict(self.settings);selected[i]=v;current=self.value
                self.configure(current,selected)
                with self.lock: self.protocol.alternate(i,v)
                self.io.transfer(EP0_READ);return
            if bm==0x80 and req==8 and not v and not i and n==1: reply=bytes([self.value])
            elif bm==0x81 and req==10 and not v and n==1 and i in self.settings: reply=bytes([self.settings[i]])
            elif bm in (0x80,0x81,0x82) and req==0 and not v and n==2:
                if bm==0x80 and not i: reply=bytes([int(bool(self.data.get('runtime',{}).get('self_powered',False))),0])
                elif bm==0x81 and i in self.settings: reply=b'\0\0'
                elif bm==0x82 and i in self.eps: reply=bytes([int(i in self.halted),0])
            elif bm==2 and req in (1,3) and not v and not n and i in self.eps:
                self.io.scalar(CLEAR_HALT if req==1 else SET_HALT,self.eps[i])
                if req==1:
                    self.halted.discard(i)
                    with self.lock: self.protocol.clear_halt(i)
                else: self.halted.add(i)
                self.io.transfer(EP0_READ);return
            if reply is not None: self.send(EP0_WRITE,reply,n,channel);return
            with self.lock: handled=self.protocol.handles(setup)
            if handled:
                data=b'' if bm&128 else self.io.transfer(EP0_READ,length=n)
                with self.lock: reply=self.protocol.control(setup,data)
                if bm&128: self.send(EP0_WRITE,reply,n,channel)
                return
            if bm in (0x80,0x81) and req==6:
                reply=self.descriptors.get((v>>8,v&255,i))
                if reply is not None: self.send(EP0_WRITE,reply,n,channel);return
            self.io.call(STALL)
        except ValueError as e:
            log(kind='protocol_error',message=str(e));self.io.call(STALL)
    def worker(self,handle,ep,stop):
        address=bytes.fromhex(ep['descriptor_hex'])[2]
        try:
            while not stop.is_set():
                if address in self.halted: stop.wait(.01);continue
                if address&128:
                    with self.lock: data=self.protocol.incoming(address)
                    if data is None and 'payload_hex' in ep: data=bytes.fromhex(ep['payload_hex'])
                    if data is not None: self.send(WRITE,data,16384,f'endpoint:{address}',handle)
                else:
                    with self.lock: size=ep.get('read_length',self.protocol.read_size(address))
                    if type(size) is not int or not 1<=size<=16384: raise ValueError('invalid read size')
                    data=self.io.transfer(READ,length=size,handle=handle)
                    if stop.is_set(): break
                    with self.lock: self.protocol.out(address,data)
                    log(kind='endpoint_out',address=address,hex=data.hex())
                stop.wait(ep.get('interval_ms',10)/1000)
        except Exception as e:
            if not stop.is_set(): log(kind='endpoint_error',address=address,message=str(e))
    def run(self):
        signal.signal(signal.SIGUSR1,lambda *_:None);signal.siginterrupt(signal.SIGUSR1,True);self.interrupts=True
        self.io=self.io or RawIO()
        try:
            init=ctypes.create_string_buffer(257)
            init[:257]=struct.pack('128s128sB',self.data['udc_driver'].encode(),self.data['udc_device'].encode(),self.data.get('speed',2))
            self.io.call(INIT,init);self.io.call(RUN);log(kind='executor_started')
            while True:
                event=ctypes.create_string_buffer(16);event[:8]=struct.pack('<II',0,8);self.io.call(FETCH,event)
                kind,n=struct.unpack('<II',event.raw[:8])
                if kind==2 and n==8:
                    try: self.control(event.raw[8:16])
                    except OSError as e:
                        log(kind='control_error',errno=e.errno,message=str(e))
                        if e.errno not in (errno.ECONNRESET,errno.ESHUTDOWN,errno.EINTR): raise
                else:
                    log(kind='usb_event',event=kind)
                    if kind in (5,6):
                        self.disable()
                        with self.lock: self.protocol.reset()
        finally:
            try: self.disable()
            finally:
                try: self.protocol.close()
                finally: self.io.close()

if __name__=='__main__': Device(DeviceConfig.load(sys.argv[1])).run()
