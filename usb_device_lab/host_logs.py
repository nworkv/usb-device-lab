import os
import select
import threading

class KernelLogs:
    def __init__(self,limit=2*1024*1024):
        self.fd=os.open('/dev/kmsg',os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
        os.lseek(self.fd,0,os.SEEK_END)
        self.limit=limit;self.records=[];self.size=0;self.lost=0;self.done=threading.Event()
        self.thread=threading.Thread(target=self._read,daemon=True);self.thread.start()
    def _read(self):
        while not self.done.is_set():
            if not select.select([self.fd],[],[],0.1)[0]:continue
            try:
                data=os.read(self.fd,8192).decode(errors='replace')
                if self.size+len(data)>self.limit:self.lost+=1
                else:self.records.append(data);self.size+=len(data)
            except BlockingIOError:pass
            except OSError:self.lost+=1
    def close(self):
        self.done.set();self.thread.join(timeout=2);os.close(self.fd)
        return {'kernel_log':''.join(self.records),'log_dropped':self.lost}
