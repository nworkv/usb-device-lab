import errno
import threading
import time
import unittest
from usb_device_lab import device as D
from usb_device_lab import raw_io as R
from usb_device_lab.device import Device
from usb_device_lab.model import DeviceConfig

CONFIG={'schema_version':1,'udc_driver':'dummy_udc','udc_device':'dummy_udc.0','speed':2,
 'descriptors':[{'type':1,'hex':'12010002000000400df00300000100000001'},{'type':2,'hex':'0902200001010080320904000002ff00000007050102400000'+'07058102400000'}],
 'runtime':{'configurations':[{'value':1,'interfaces':[{'number':0,'alternates':[{'setting':0,'endpoints':[
   {'descriptor_hex':'07050102400000','read_length':64},{'descriptor_hex':'07058102400000','payload_hex':'00'}]}]}]}]}}

class BlockingIO:
    """READ/WRITE block until the endpoint is disabled, like Raw Gadget with an idle host."""
    def __init__(self):
        self.events=[];self.lock=threading.Lock();self.handle=0;self.disabled={};self.blocked=threading.Semaphore(0)
    def record(self,e):
        with self.lock: self.events.append(e)
    def call(self,request,arg=None):
        self.record(('call',request))
        if request==R.ENABLE:
            with self.lock: self.handle+=1;h=self.handle;self.disabled[h]=threading.Event();return h
        return 0
    def scalar(self,request,value):
        self.record(('scalar',request,value))
        if request==R.DISABLE: self.disabled[value].set()
    def transfer(self,request,data=b'',length=None,handle=0):
        if request in (R.READ,R.WRITE):
            self.blocked.release()
            if not self.disabled[handle].wait(10): raise AssertionError('endpoint never disabled')
            self.record(('unblocked',handle));raise OSError(errno.ESHUTDOWN,'endpoint disabled')
        return b''
    def close(self): pass

class ShutdownOrderTests(unittest.TestCase):
    def setUp(self):
        self.io=BlockingIO();self.device=Device(DeviceConfig(CONFIG),self.io)
        self.device.configure(1)
        for _ in range(2): self.assertTrue(self.io.blocked.acquire(timeout=3))
    def test_disable_does_not_hang_on_blocked_endpoints(self):
        threads=[t for _,_,t in self.device.workers]
        started=time.monotonic();self.device.disable()
        self.assertLess(time.monotonic()-started,D.JOIN_TIMEOUT)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual((self.device.workers,self.device.eps),([],{}))
    def test_stop_then_ep_disable_then_join(self):
        workers=list(self.device.workers);self.device.disable()
        disables=[i for i,e in enumerate(self.io.events) if e[0]=='scalar' and e[1]==R.DISABLE]
        unblocked=[i for i,e in enumerate(self.io.events) if e[0]=='unblocked']
        self.assertEqual(len(disables),2);self.assertEqual(len(unblocked),2)
        self.assertLess(disables[0],min(unblocked))
        self.assertTrue(all(stop.is_set() for _,stop,_ in workers))
    def test_reconfigure_after_disable(self):
        self.device.disable();self.device.configure(1)
        self.assertEqual(len(self.device.eps),2);self.device.disable()
    def test_stuck_thread_is_reported(self):
        stuck=threading.Event();t=threading.Thread(target=stuck.wait,daemon=True);t.start()
        self.device.disable()
        self.device.workers=[(99,threading.Event(),t)];self.io.disabled[99]=threading.Event()
        old=D.JOIN_TIMEOUT;D.JOIN_TIMEOUT=0.05
        try:
            with self.assertRaises(RuntimeError): self.device.disable()
        finally: D.JOIN_TIMEOUT=old;stuck.set()
        self.assertIn(('scalar',R.DISABLE,99),self.io.events)

if __name__=='__main__': unittest.main()
