import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from .host_logs import KernelLogs
from .rpc import Lines

def emit(value): print(json.dumps(value),flush=True)
def main():
    p=argparse.ArgumentParser();p.add_argument('--bus',type=int,required=True);p.add_argument('--collector',required=True);a=p.parse_args()
    if not 1<=a.bus<=255: raise SystemExit('bus must be 1..255')
    namespace=f'{os.uname().release}:{Path("/proc/sys/kernel/random/boot_id").read_text().strip()}:{a.bus}'
    child=logs=None
    with tempfile.TemporaryFile(mode='w+') as stderr:
        try:
            emit({'ready':True,'namespace':namespace})
            for line in sys.stdin:
                command=json.loads(line).get('command')
                if command=='start' and child is None:
                    logs=KernelLogs()
                    child=subprocess.Popen([a.collector,str(a.bus)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1)
                    rpc=Lines(child)
                    if not rpc.receive().get('ready'): raise RuntimeError('collector not ready')
                    rpc.send('start')
                    if not rpc.receive().get('started'): raise RuntimeError('collector not started')
                    emit({'started':True,'namespace':namespace})
                elif command=='stop' and child is not None:
                    rpc.send('stop');result=rpc.receive(timeout=30)
                    child.stdin.close();child.wait(timeout=3)
                    if child.returncode: raise RuntimeError('collector failed')
                    child=None;result.update(logs.close());logs=None
                    result['pcs']=sorted(set(result['pcs']));result['namespace']=namespace
                    result['coverage_valid']=bool(result['pcs']) and not result['saturated']
                    emit(result)
                else: raise ValueError('invalid agent state')
        except Exception as e:
            stderr.seek(0);emit({'error':str(e),'collector_stderr':stderr.read(16384)});return 1
        finally:
            if child is not None: child.kill();child.wait(timeout=3)
            if logs is not None: logs.close()
    return 0
if __name__=='__main__': sys.exit(main())
