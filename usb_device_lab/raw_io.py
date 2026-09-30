import ctypes
import os
import struct

def code(direction,n,size=0): return direction<<30|size<<16|ord('U')<<8|n
INIT=code(1,0,257);RUN=code(0,1);FETCH=code(2,2,8)
EP0_WRITE=code(1,3,8);EP0_READ=code(3,4,8);ENABLE=code(1,5,9);DISABLE=code(1,6,4)
WRITE=code(1,7,8);READ=code(3,8,8);CONFIGURE=code(0,9);STALL=code(0,12)
SET_HALT=code(1,13,4);CLEAR_HALT=code(1,14,4)
MAX_TRANSFER=65535
libc=ctypes.CDLL(None,use_errno=True)
libc.ioctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_void_p];libc.ioctl.restype=ctypes.c_int

class RawIO:
    def __init__(self,path='/dev/raw-gadget'): self.fd=os.open(path,os.O_RDWR|os.O_CLOEXEC)
    def call(self,request,arg=None):
        rc=libc.ioctl(self.fd,request,None if arg is None else ctypes.cast(arg,ctypes.c_void_p))
        if rc<0:
            e=ctypes.get_errno();raise OSError(e,os.strerror(e))
        return rc
    def scalar(self,request,value): return self.call(request,ctypes.c_void_p(value))
    def transfer(self,request,data=b'',length=None,handle=0):
        n=len(data) if length is None else length
        if not 0<=n<=MAX_TRANSFER or len(data)>n: raise ValueError('invalid transfer length')
        buf=ctypes.create_string_buffer(8+n)
        buf[:8]=struct.pack('<HHI',handle,1 if request==EP0_WRITE else 0,n)
        buf[8:8+len(data)]=data
        rc=self.call(request,buf);return buf.raw[8:8+max(0,min(rc,n))]
    def close(self):
        if self.fd>=0: os.close(self.fd);self.fd=-1
