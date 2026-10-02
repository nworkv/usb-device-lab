import struct
import unittest
from usb_device_lab import raw_io as R

class RawIOAbiTests(unittest.TestCase):
    def test_uapi_ioctl_layout(self):
        def expected(direction,number,size=0):
            return direction<<30|size<<16|ord('U')<<8|number
        cases=((R.INIT,R.IOC_WRITE,0,257),(R.RUN,R.IOC_NONE,1,0),
               (R.FETCH,R.IOC_READ,2,8),(R.EP0_WRITE,R.IOC_WRITE,3,8),
               (R.EP0_READ,R.IOC_READWRITE,4,8),(R.ENABLE,R.IOC_WRITE,5,9),
               (R.WRITE,R.IOC_WRITE,7,8),(R.READ,R.IOC_READWRITE,8,8))
        for actual,direction,number,size in cases:
            self.assertEqual(actual,expected(direction,number,size))
        self.assertEqual((R.WRITE>>16)&0x3fff,R.RAW_EP_IO_SIZE)
        self.assertEqual((R.READ>>30)&3,R.IOC_READWRITE)
    def test_ioctl_input_limits(self):
        self.assertEqual(R.code(R.IOC_WRITE,255,16383),1<<30|16383<<16|ord('U')<<8|255)
        for args in ((4,1,0),(1,-1,0),(1,1,16384),(True,1,0)):
            with self.subTest(args=args),self.assertRaises(ValueError): R.code(*args)
    def make_io(self):
        io=R.RawIO.__new__(R.RawIO)
        seen=[]
        def call(request,buf):
            seen.append((request,bytes(buf.raw)))
            return 0
        io.call=call
        return io,seen
    def test_write_header_and_payload(self):
        io,seen=self.make_io()
        self.assertEqual(io.transfer(R.WRITE,b'abc',handle=0x1234),b'')
        request,raw=seen.pop()
        self.assertEqual(request,R.WRITE)
        self.assertEqual(struct.unpack('<HHI',raw[:8]),(0x1234,0,3))
        self.assertEqual(raw[8:],b'abc\0')
    def test_read_header_and_result(self):
        io=R.RawIO.__new__(R.RawIO)
        def call(request,buf):
            self.assertEqual(request,R.READ)
            self.assertEqual(struct.unpack('<HHI',buf.raw[:8]),(7,0,4))
            buf[8:12]=b'pong'
            return 4
        io.call=call
        self.assertEqual(io.transfer(R.READ,length=4,handle=7),b'pong')
    def test_transfer_rejects_ambiguous_or_invalid_arguments(self):
        io,_=self.make_io()
        bad=((R.FETCH,b'',None,0),(R.WRITE,b'a',2,1),(R.READ,b'a',1,1),
             (R.READ,b'',-1,1),(R.WRITE,b'',None,-1),(R.WRITE,'text',None,1))
        for request,data,length,handle in bad:
            with self.subTest(request=request,data=data,length=length,handle=handle),self.assertRaises(ValueError):
                io.transfer(request,data,length,handle)
    def test_scalar_range(self):
        io=R.RawIO.__new__(R.RawIO)
        io.call=lambda request,arg: 0
        for value in (-1,1<<32,True):
            with self.subTest(value=value),self.assertRaises(ValueError): io.scalar(R.DISABLE,value)

if __name__=='__main__': unittest.main()
