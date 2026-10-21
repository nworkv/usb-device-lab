"""Runner cleanup closes RPC and executor pipes after child termination."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab import runner

class FakeChild:
    def __init__(self,argv,**kw):
        self.argv=argv;self.stdin=tempfile.TemporaryFile('w+b');self.stdout=tempfile.TemporaryFile('w+b');self.stderr=tempfile.TemporaryFile('w+b');self.pid=777;self.returncode=None
    def poll(self):return self.returncode
    def wait(self,timeout=None):self.returncode=0;return 0
    def kill(self):self.returncode=-9
class Reader:
    owns_stdout=True
    def __init__(self,p):self.p=p
    def receive(self,timeout=15):return {'ready':True,'started':True,'namespace':'n'}
    def send(self,v):pass
    def close(self):self.p.stdout.close()
class RunnerCleanupTests(unittest.TestCase):
    def test_executor_closes_every_subprocess_pipe(self):
        children=[];closed=[]
        def popen(argv,**kw):c=FakeChild(argv,**kw);children.append(c);return c
        def cleanup(p,rpc=None):
            closed.append(p)
            if rpc:rpc.close()
            for n in ('stdin','stdout','stderr'):
                if n=='stdout' and rpc:continue
                getattr(p,n).close()
            return []
        with tempfile.TemporaryDirectory() as tmp,patch.object(runner.subprocess,'Popen',side_effect=popen),patch.object(runner,'Lines',Reader),patch.object(runner,'close_rpc_streams',side_effect=cleanup),patch.object(runner.time,'sleep'),patch.object(runner,'classify',side_effect=lambda r:r):
            path=Path(tmp);(path/'agent.log').touch();(path/'executor.log').touch();result=runner.execute(path,['agent'],['gadget'],0)
        self.assertEqual([c.argv for c in children],[['agent'],['gadget']]);self.assertCountEqual(closed,children);self.assertTrue(all(c.stdin.closed and c.stdout.closed and c.stderr.closed for c in children));self.assertTrue(result['coverage_valid'])

if __name__=='__main__':
    unittest.main()
