import errno
import http.client
import io
import json
import os
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from usb_device_lab import raw_io as R
from usb_device_lab.device import Device
from usb_device_lab.errors import classify
from usb_device_lab.host_agent import main as agent_main
from usb_device_lab.model import DeviceConfig
from usb_device_lab.runner import campaign
from usb_device_lab.storage import Store
from usb_device_lab.web import make_server

ROOT=Path(__file__).resolve().parents[1]
DEVICE={'schema_version':1,'udc_driver':'dummy_udc','udc_device':'dummy_udc.0','speed':2,
 'descriptors':[{'type':1,'hex':'12010002000000400df00300000100000001'},{'type':2,'hex':'09022000010100803209040000000000000000'}],
 'runtime':{'configurations':[{'value':1,'interfaces':[
   {'number':0,'alternates':[{'setting':0,'endpoints':[{'descriptor_hex':'07050102400000','read_length':64},{'descriptor_hex':'07058102400000'}]}]},
   {'number':1,'alternates':[{'setting':0,'endpoints':[]},{'setting':1,'endpoints':[{'descriptor_hex':'0705820308000a','payload_hex':'00'}]}]}]}]},
 'protocol':{'name':'script','rules':[{'event':'out','address':1,'capture':'packet','send':[{'address':129,'var':'packet'}]}]}}

class FakeIO:
    def __init__(self,reads=()):
        self.calls=[];self.writes=[];self.handle=0;self.lock=threading.Lock();self.reads=deque(reads)
    def call(self,request,arg=None):
        with self.lock:
            self.calls.append(request)
            if request==R.ENABLE:
                self.handle+=1;return self.handle
        return 0
    def scalar(self,request,value):
        with self.lock: self.calls.append((request,value))
    def transfer(self,request,data=b'',length=None,handle=0):
        if request==R.READ:
            with self.lock:
                if self.reads: return self.reads.popleft()
            time.sleep(0.01);raise OSError(errno.ESHUTDOWN,'closed')
        with self.lock: self.writes.append((request,bytes(data),handle))
        return b''
    def close(self): pass

def control(device,bm,req,value,index,length):
    with redirect_stdout(io.StringIO()): device.control(struct.pack('<BBHHH',bm,req,value,index,length))

def wait_for(predicate,timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return True
        time.sleep(0.01)
    return False

class DeviceTests(unittest.TestCase):
    def make(self,reads=()):
        fake=FakeIO(reads);device=Device(DeviceConfig(DEVICE),fake);self.addCleanup(device.disable);return fake,device
    def test_descriptor_is_clipped_to_wlength(self):
        fake,device=self.make();control(device,0x80,6,0x0100,0,8)
        self.assertEqual(fake.writes[-1],(R.EP0_WRITE,bytes.fromhex(DEVICE['descriptors'][0]['hex'])[:8],0))
    def test_configuration_and_alternate_settings(self):
        fake,device=self.make()
        control(device,0,9,1,0,0);self.assertEqual((device.value,device.settings,len(device.eps)),(1,{0:0,1:0},2))
        control(device,0x80,8,0,0,1);self.assertEqual(fake.writes[-1][1],b'\x01')
        control(device,1,11,1,1,0);self.assertEqual((device.settings[1],len(device.eps)),(1,3))
        control(device,0x81,10,0,1,1);self.assertEqual(fake.writes[-1][1],b'\x01')
        control(device,0,9,0,0,0);self.assertEqual((device.value,device.eps),(0,{}))
    def test_invalid_requests_stall(self):
        fake,device=self.make();control(device,0,9,1,0,0)
        for request in ((1,11,9,1,0),(0x80,6,0x0900,0,8),(0,9,7,0,0),(1,11,0,7,0)):
            control(device,*request);self.assertEqual(fake.calls[-1],R.STALL,request)
    def test_halt_and_status(self):
        fake,device=self.make();control(device,0,9,1,0,0)
        control(device,2,3,0,1,0);self.assertIn(1,device.halted)
        control(device,0x82,0,0,1,2);self.assertEqual(fake.writes[-1][1],b'\x01\x00')
        control(device,2,1,0,1,0);self.assertNotIn(1,device.halted)
    def test_bulk_echo_through_script_protocol(self):
        fake,device=self.make([b'hello']);control(device,0,9,1,0,0)
        in_handle=device.eps[0x81]
        self.assertTrue(wait_for(lambda: (R.WRITE,b'hello',in_handle) in fake.writes),fake.writes)

class AgentTests(unittest.TestCase):
    def test_agent_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            collector=Path(directory)/'collector'
            collector.write_text('#!%s\nimport sys\nprint(\'{"ready":true}\',flush=True)\nfor line in sys.stdin:\n    if line=="start\\n": print(\'{"started":true}\',flush=True)\n    elif line=="stop\\n": print(\'{"saturated":false,"pcs":["0x2","0x1","0x1"]}\',flush=True)\n'%sys.executable)
            collector.chmod(collector.stat().st_mode|stat.S_IXUSR)
            class Logs:
                def close(self): return {'kernel_log':'BUG: test\n','log_dropped':0}
            out=io.StringIO()
            with mock.patch('sys.argv',['agent','--bus','1','--collector',str(collector)]),\
                 mock.patch('sys.stdin',io.StringIO('{"command":"start"}\n{"command":"stop"}\n')),\
                 mock.patch('usb_device_lab.host_agent.KernelLogs',Logs),redirect_stdout(out):
                self.assertEqual(agent_main(),0)
            lines=[json.loads(x) for x in out.getvalue().splitlines()]
            self.assertTrue(lines[0]['ready'] and lines[1]['started'])
            self.assertEqual(lines[2]['pcs'],['0x1','0x2']);self.assertTrue(lines[2]['coverage_valid'])
            self.assertTrue(lines[2]['namespace'].endswith(':1'));self.assertEqual(lines[2]['kernel_log'],'BUG: test\n')
    def test_invalid_state_reports_error(self):
        out=io.StringIO()
        with mock.patch('sys.argv',['agent','--bus','1','--collector','/bin/true']),\
             mock.patch('sys.stdin',io.StringIO('{"command":"stop"}\n')),redirect_stdout(out):
            self.assertEqual(agent_main(),1)
        self.assertIn('invalid agent state',json.loads(out.getvalue().splitlines()[-1])['error'])

class ClassifierTests(unittest.TestCase):
    def test_classification(self):
        result=classify({'kernel_log':'[1] BUG: KASAN: x at 0xabc\n[2] WARNING: y\n','log_dropped':1,
            'executor_log':'{"kind": "endpoint_error"}\n','executor_returncode':3,'coverage_valid':False,'pcs':[]})
        self.assertEqual(sorted(e['kind'] for e in result['errors']),['coverage_unavailable','executor','executor_io','kernel','kernel','log_loss'])
    def test_infrastructure_error_is_not_duplicated(self):
        result=classify({'errors':[{'kind':'infrastructure','summary':'x'}],'pcs':[]})
        self.assertEqual([e['kind'] for e in result['errors']],['infrastructure'])

FAKE_AGENT="""
import json,os,sys
state=os.environ['FAKE_STATE'];mode=os.environ.get('FAKE_MODE','normal')
def emit(v): print(json.dumps(v),flush=True)
emit({'ready':True,'namespace':'k:boot:1'})
for line in sys.stdin:
    command=json.loads(line)['command']
    if command=='start': emit({'started':True,'namespace':'k:boot:1'})
    elif command=='stop':
        n=int(open(state).read()) if os.path.exists(state) else 0
        open(state,'w').write(str(n+1))
        pcs=[] if mode=='empty' else [['0x1','0x2'],['0x1','0x2'],['0x3']][min(n,2)]
        log='BUG: KASAN: use-after-free at 0xdeadbeef\\n' if n==1 else ''
        emit({'saturated':False,'pcs':pcs,'namespace':'k:boot:1','coverage_valid':bool(pcs),'kernel_log':log,'log_dropped':0})
"""
FAKE_GADGET="import time\nprint('{\"kind\": \"executor_started\"}',flush=True)\ntime.sleep(30)\n"
SEED={k:v for k,v in DEVICE.items() if k not in ('runtime','protocol')}
SEED['runtime']={'configurations':[{'value':1,'interfaces':[{'number':0,'alternates':[{'setting':0,'endpoints':[]}]}]}]}

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name)
        (self.root/'fake_agent.py').write_text(FAKE_AGENT);(self.root/'fake_gadget.py').write_text(FAKE_GADGET)
        (self.root/'seed.json').write_text(json.dumps(SEED))
        environment={'FAKE_STATE':str(self.root/'counter'),'PYTHONPATH':os.pathsep.join([str(ROOT),str(self.root)])}
        patcher=mock.patch.dict(os.environ,environment);patcher.start();self.addCleanup(patcher.stop)
        self.agent=[sys.executable,str(self.root/'fake_agent.py')]
    def run_campaign(self,name,agent,iterations=3):
        with redirect_stdout(io.StringIO()):
            return campaign([str(self.root/'seed.json')],str(self.root/name),agent,iterations,0.4,7,gadget_module='fake_gadget')
    def test_coverage_guided_corpus_and_errors(self):
        outcomes=self.run_campaign('state',self.agent)
        self.assertEqual([o['new_pcs'] for o in outcomes],[2,0,1]);self.assertEqual([o['errors']>0 for o in outcomes],[False,True,False])
        store=Store(self.root/'state');self.addCleanup(store.close)
        self.assertEqual(len(store.corpus()),2)
        self.assertEqual(store.db.execute('SELECT COUNT(*),SUM(occurrences) FROM errors').fetchone()[:],(1,1))
        self.assertEqual([r[0] for r in store.db.execute('SELECT status FROM runs ORDER BY started')],['ok','error','ok'])
    def test_missing_coverage_stops_campaign(self):
        with mock.patch.dict(os.environ,{'FAKE_MODE':'empty'}): outcomes=self.run_campaign('empty',self.agent)
        self.assertEqual(len(outcomes),1);self.assertGreater(outcomes[0]['errors'],0)
    def test_agent_failure_stops_campaign(self):
        outcomes=self.run_campaign('bad',[sys.executable,'-c','import sys;sys.exit(3)'])
        self.assertEqual(len(outcomes),1)

class Config:
    def __init__(self,number): self.digest='d%d'%number;self.number=number
    def canonical(self): return json.dumps({'n':self.number})

class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        store=Store(Path(self.directory.name)/'state');self.ids=[]
        for number in range(3):
            config=Config(number);ident=store.begin(config,{'iteration':number});self.ids.append(ident)
            store.finish(ident,config,{'errors':[{'kind':'kernel','summary':'BUG: x'}] if number==1 else [],'pcs':[]})
        store.close()
        self.server=make_server(Path(self.directory.name)/'state',0);self.port=self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
    def get(self,path,host=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port);connection.request('GET',path,headers={'Host':host} if host else {})
        response=connection.getresponse();return response.status,response.read()
    def test_api(self):
        status,body=self.get('/');self.assertEqual(status,200);self.assertIn(b'USB Device Lab',body)
        self.assertEqual(len(json.loads(self.get('/api/runs')[1])),3)
        self.assertEqual([r['id'] for r in json.loads(self.get('/api/runs?status=error')[1])],[self.ids[1]])
        self.assertEqual(len(json.loads(self.get('/api/runs?offset=2')[1])),1)
        self.assertEqual(self.get('/api/runs?offset=abc')[0],400)
        self.assertEqual(json.loads(self.get('/api/errors')[1])[0]['kind'],'kernel')
        detail=json.loads(self.get('/api/run/'+self.ids[1])[1]);self.assertEqual(detail['metadata']['iteration'],1)
        self.assertEqual(json.loads(self.get('/api/config/'+self.ids[2])[1]),{'n':2})
    def test_rejects_unknown_paths_and_hosts(self):
        for path in ('/api/run/'+'0'*32,'/api/run/../../etc/passwd','/api/config/%2e%2e%2f%2e%2e','/nope'): self.assertEqual(self.get(path)[0],404,path)
        self.assertEqual(self.get('/',host='evil.example')[0],403)
        self.assertEqual(self.server.server_address[0],'127.0.0.1')

if __name__=='__main__': unittest.main()
