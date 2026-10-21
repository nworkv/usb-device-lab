"""RPC reader shutdown owns stdout and closes all subprocess streams."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from usb_device_lab.rpc import Lines,close_rpc_streams

class RPCChildTests(unittest.TestCase):
    def spawn(self,code):
        child=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=False,bufsize=0,start_new_session=True)
        child.stdout_is_pipe=True
        return child

    def test_jsonl_start_stop_then_all_three_pipes_closed(self):
        code="import json,sys\nfor line in sys.stdin.buffer:\n c=json.loads(line)['command']\n print(json.dumps({'ready':True} if c=='hello' else {'started':True} if c=='start' else {'analysis_complete':True,'capture_id':'a'*32,'kernel_candidate_count':0}),flush=True)\n"
        child=self.spawn(code);rpc=Lines(child)
        rpc.send({'command':'hello'});self.assertTrue(rpc.receive(timeout=2)['ready'])
        rpc.send({'command':'start'});self.assertTrue(rpc.receive(timeout=2)['started'])
        rpc.send({'command':'stop'});self.assertTrue(rpc.receive(timeout=2)['analysis_complete'])
        child.stdin.close();child.wait(timeout=2)
        self.assertEqual(close_rpc_streams(child,rpc),[])
        self.assertTrue(child.stdin.closed and child.stdout.closed and child.stderr.closed)

    def test_large_record_is_chunked_and_bounded(self):
        child=self.spawn("import json;print(json.dumps({'blob':'x'*200000}),flush=True)")
        rpc=Lines(child);result=rpc.receive(timeout=3)
        self.assertEqual(len(result['blob']),200000)
        child.wait(timeout=2)
        self.assertEqual(close_rpc_streams(child,rpc),[])

    def test_oversized_record_rejected_then_cleanup_closes_streams(self):
        child=self.spawn("import os;os.write(1,b'x'*(32*1024*1024+1))")
        rpc=Lines(child)
        with self.assertRaisesRegex(ValueError,'oversized'):rpc.receive(timeout=10)
        child.kill();child.wait(timeout=2)
        self.assertEqual(close_rpc_streams(child,rpc),[])
        self.assertTrue(child.stdout.closed and child.stdin.closed and child.stderr.closed)

    def test_eof_and_malformed_protocol_fail_closed(self):
        for code,error in (("pass",EOFError),("print('[]',flush=True)",ValueError),("import os;os.write(1,b'{')",ValueError)):
            child=self.spawn(code);rpc=Lines(child)
            with self.assertRaises(error):rpc.receive(timeout=2)
            child.wait(timeout=2)
            self.assertEqual(close_rpc_streams(child,rpc),[])
