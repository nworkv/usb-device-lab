import json
import queue
import threading

class Lines:
    def __init__(self,process):
        self.process=process;self.queue=queue.Queue(maxsize=4)
        threading.Thread(target=self.read,daemon=True).start()
    def read(self):
        try:
            while True:
                line=self.process.stdout.readline(32*1024*1024)
                if not line:raise EOFError('agent disconnected')
                if not line.endswith('\n'):raise ValueError('truncated/oversized protocol line')
                value=json.loads(line)
                if not isinstance(value,dict):raise ValueError('protocol object required')
                self.queue.put(value)
        except Exception as e:self.queue.put(e)
    def receive(self,timeout=15):
        try:result=self.queue.get(timeout=timeout)
        except queue.Empty as e:raise TimeoutError('agent timeout') from e
        if isinstance(result,Exception):raise result
        if result.get('error'):raise RuntimeError(result['error'])
        return result
    def send(self,value):
        self.process.stdin.write((value if isinstance(value,str) else json.dumps(value))+'\n');self.process.stdin.flush()
