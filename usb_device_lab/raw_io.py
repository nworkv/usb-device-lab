import ctypes
import os
import struct

IOC_NRBITS=8
IOC_TYPEBITS=8
IOC_SIZEBITS=14
IOC_DIRBITS=2
IOC_NRSHIFT=0
IOC_TYPESHIFT=IOC_NRSHIFT+IOC_NRBITS
IOC_SIZESHIFT=IOC_TYPESHIFT+IOC_TYPEBITS
IOC_DIRSHIFT=IOC_SIZESHIFT+IOC_SIZEBITS
IOC_NONE=0
IOC_WRITE=1
IOC_READ=2
IOC_READWRITE=IOC_READ|IOC_WRITE
IOC_MAXNR=(1<<IOC_NRBITS)-1
IOC_MAXSIZE=(1<<IOC_SIZEBITS)-1

# Raw Gadget UAPI uses _IO{,W,R,WR}('U', number, struct ...).  For EP I/O
# the encoded ioctl size is sizeof(struct usb_raw_ep_io) == 8: data[] is a
# flexible array and the actual transfer size is stored in its length field.
def code(direction,n,size=0):
    if type(direction) is not int or not IOC_NONE<=direction<(1<<IOC_DIRBITS): raise ValueError('invalid ioctl direction')
    if type(n) is not int or not 0<=n<=IOC_MAXNR: raise ValueError('invalid ioctl number')
    if type(size) is not int or not 0<=size<=IOC_MAXSIZE: raise ValueError('invalid ioctl size')
    return direction<<IOC_DIRSHIFT|size<<IOC_SIZESHIFT|ord('U')<<IOC_TYPESHIFT|n

RAW_INIT_SIZE=257
RAW_EVENT_SIZE=8
RAW_EP_IO_SIZE=8
RAW_EP_DESCRIPTOR_SIZE=9
INIT=code(IOC_WRITE,0,RAW_INIT_SIZE)
RUN=code(IOC_NONE,1)
FETCH=code(IOC_READ,2,RAW_EVENT_SIZE)
EP0_WRITE=code(IOC_WRITE,3,RAW_EP_IO_SIZE)
EP0_READ=code(IOC_READWRITE,4,RAW_EP_IO_SIZE)
ENABLE=code(IOC_WRITE,5,RAW_EP_DESCRIPTOR_SIZE)
DISABLE=code(IOC_WRITE,6,4)
WRITE=code(IOC_WRITE,7,RAW_EP_IO_SIZE)
READ=code(IOC_READWRITE,8,RAW_EP_IO_SIZE)
CONFIGURE=code(IOC_NONE,9)
STALL=code(IOC_NONE,12)
SET_HALT=code(IOC_WRITE,13,4)
CLEAR_HALT=code(IOC_WRITE,14,4)
MAX_TRANSFER=65535
WRITE_REQUESTS={EP0_WRITE,WRITE}
READ_REQUESTS={EP0_READ,READ}
TRANSFER_REQUESTS=WRITE_REQUESTS|READ_REQUESTS

libc=ctypes.CDLL(None,use_errno=True)
libc.ioctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_void_p]
libc.ioctl.restype=ctypes.c_int

class RawIO:
    def __init__(self,path='/dev/raw-gadget'):
        self.fd=os.open(path,os.O_RDWR|os.O_CLOEXEC)
    def call(self,request,arg=None):
        rc=libc.ioctl(self.fd,request,None if arg is None else ctypes.cast(arg,ctypes.c_void_p))
        if rc<0:
            e=ctypes.get_errno()
            raise OSError(e,os.strerror(e))
        return rc
    def scalar(self,request,value):
        if type(value) is not int or not 0<=value<=0xffffffff: raise ValueError('invalid scalar value')
        return self.call(request,ctypes.c_void_p(value))
    def transfer(self,request,data=b'',length=None,handle=0):
        if request not in TRANSFER_REQUESTS: raise ValueError('invalid transfer request')
        if type(handle) is not int or not 0<=handle<=0xffff: raise ValueError('invalid endpoint handle')
        try: data=bytes(data)
        except (TypeError,ValueError) as e: raise ValueError('transfer data must be bytes-like') from e
        n=len(data) if length is None else length
        if type(n) is not int or not 0<=n<=MAX_TRANSFER: raise ValueError('invalid transfer length')
        if request in WRITE_REQUESTS:
            if len(data)!=n: raise ValueError('write payload length must equal transfer length')
        elif data:
            raise ValueError('read transfer must not include payload data')
        buf=ctypes.create_string_buffer(RAW_EP_IO_SIZE+n)
        buf[:RAW_EP_IO_SIZE]=struct.pack('<HHI',handle,0,n)
        buf[RAW_EP_IO_SIZE:RAW_EP_IO_SIZE+len(data)]=data
        rc=self.call(request,buf)
        return buf.raw[RAW_EP_IO_SIZE:RAW_EP_IO_SIZE+max(0,min(rc,n))]
    def close(self):
        if self.fd>=0:
            os.close(self.fd)
            self.fd=-1
