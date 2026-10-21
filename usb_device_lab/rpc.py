import json
import os
import queue
import selectors
import threading

class Lines:
    MAX_LINE_BYTES=32*1024*1024
    owns_stdout=True
    def __init__(self,process):
        if getattr(process,'stdout_is_pipe',False) is not True:
            raise ValueError('RPC requires a dedicated subprocess stdout pipe')
        self.process=process;self.queue=queue.Queue(maxsize=4);self.stop_event=threading.Event();self.close_error=None
        self.thread=threading.Thread(target=self.read,name='agent-rpc',daemon=True);self.thread.start()
    def put(self,value):
        while not self.stop_event.is_set():
            try:self.queue.put(value,timeout=.05);return True
            except queue.Full:continue
        return False
    def read(self):
        selector=None;error=None;pending=bytearray()
        try:
            fd=self.process.stdout.fileno();os.set_blocking(fd,False);selector=selectors.DefaultSelector();selector.register(fd,selectors.EVENT_READ)
            while not self.stop_event.is_set():
                if not selector.select(.1):continue
                try:data=os.read(fd,65536)
                except BlockingIOError:continue
                if not data:
                    if pending:raise ValueError('truncated/oversized protocol line')
                    raise EOFError('agent disconnected')
                pending.extend(data)
                while True:
                    end=pending.find(b'\n')
                    if end<0:
                        if len(pending)>=self.MAX_LINE_BYTES:raise ValueError('truncated/oversized protocol line')
                        break
                    length=end+1
                    if length>self.MAX_LINE_BYTES:raise ValueError('truncated/oversized protocol line')
                    line=bytes(pending[:length]);del pending[:length]
                    value=json.loads(line.decode('utf-8'))
                    if not isinstance(value,dict):raise ValueError('protocol object required')
                    if not self.put(value):return
        except Exception as caught:error=caught
        finally:
            if selector is not None:
                try:selector.close()
                except Exception as caught:
                    self.close_error=caught
                    if error is None:error=caught
            if self.owns_stdout:
                try:self.process.stdout.close()
                except Exception as caught:
                    self.close_error=caught
                    if error is None:error=caught
            if error is not None:self.put(error)
    def close(self,timeout=2):
        self.stop_event.set()
        try:self.queue.put_nowait(EOFError('RPC reader closed'))
        except queue.Full:pass
        self.thread.join(timeout)
        if self.thread.is_alive():raise RuntimeError('RPC reader did not stop; stdout remains owned by the reader')
        if self.close_error is not None:raise RuntimeError('RPC stream cleanup failed: '+str(self.close_error)) from self.close_error
    def receive(self,timeout=15):
        if self.stop_event.is_set():raise EOFError('RPC reader closed')
        try:result=self.queue.get(timeout=timeout)
        except queue.Empty as error:raise TimeoutError('agent timeout') from error
        if self.stop_event.is_set():raise EOFError('RPC reader closed')
        if isinstance(result,Exception):raise result
        if result.get('error'):raise RuntimeError(result['error'])
        return result
    def send(self,value):
        if self.stop_event.is_set():raise EOFError('RPC reader closed')
        line=value if isinstance(value,str) else json.dumps(value)
        self.process.stdin.write((line+'\n').encode('utf-8'));self.process.stdin.flush()

def close_rpc_streams(process,rpc=None):
    errors=[]
    if rpc is not None and getattr(rpc,'close_error',None) is not None:
        errors.append('RPC close: '+str(rpc.close_error))
    close=getattr(rpc,'close',None)
    if callable(close):
        try:close()
        except Exception as error:
            message='RPC close: '+str(getattr(rpc,'close_error',None) or error)
            if message not in errors:errors.append(message)
    if process is None:return errors
    for name in ('stdin','stdout','stderr'):
        if name=='stdout' and (getattr(rpc,'owns_stdout',False) is True or getattr(process,'stdout_is_pipe',False) is False):continue
        stream=getattr(process,name,None)
        if stream is None:continue
        try:stream.close()
        except Exception as error:errors.append(name+' close: '+str(error))
    return errors
