import json
import tempfile
import unittest
from pathlib import Path
from usb_device_lab.model import DeviceConfig
from usb_device_lab.topology import validate_topology,active_endpoints
from usb_device_lab.mutator import Mutator,WireMutator
from usb_device_lab.protocol import Script,load_protocol
from usb_device_lab.storage import Store

class Tests(unittest.TestCase):
    def setUp(self): self.c=DeviceConfig.load(Path(__file__).resolve().parents[1]/'examples/composite.json')
    def test_roundtrip(self): self.assertEqual(self.c.digest,DeviceConfig(json.loads(self.c.canonical())).digest)
    def test_bad_wire_allowed(self):
        d=json.loads(self.c.canonical());d['descriptors'][0]['hex']='ff';DeviceConfig(d)
    def test_bad_hex(self):
        d=json.loads(self.c.canonical());d['descriptors'][0]['hex']='zz'
        with self.assertRaises(ValueError): DeviceConfig(d)
    def test_alternates(self):
        c=validate_topology(self.c.data)[1]
        self.assertEqual(len(active_endpoints(c,{0:0,1:0})),2)
        self.assertEqual(len(active_endpoints(c,{0:0,1:1})),3)
    def test_mutation(self):
        before=self.c.canonical();a,ta=Mutator(7).mutate(self.c);b,tb=Mutator(7).mutate(self.c)
        self.assertEqual(a.digest,b.digest);self.assertEqual(ta,tb);self.assertEqual(before,self.c.canonical())
        self.assertEqual(a.data['runtime'],self.c.data['runtime'])
    def test_many_mutations(self):
        m=Mutator(18);c=self.c
        for _ in range(100): c,_=m.mutate(c);c.validate()
    def test_wire(self):
        a=WireMutator({'seed':3,'probability':1});b=WireMutator({'seed':3,'probability':1})
        a.apply('other',b'x')
        for _ in range(30): self.assertEqual(a.apply('in',b'abc'),b.apply('in',b'abc'))
    def test_echo(self):
        s=Script(self.c.data['protocol']);s.out(1,b'abc');self.assertEqual(s.incoming(129),b'abc');self.assertIsNone(s.incoming(129))
    def test_state(self):
        s=Script({'rules':[{'event':'control','state':'start','match':{'bRequest':1},'capture':'x','next_state':'saved'},
            {'event':'control','state':'saved','match':{'bRequest':2},'reply_var':'x'}]})
        s.control({'bRequest':1},b'abc');self.assertEqual(s.control({'bRequest':2},b''),b'abc');s.reset();self.assertEqual(s.state,'start')
    def test_plugins_restricted(self):
        with self.assertRaises(ValueError): load_protocol({'name':'os:system'})
    def test_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d)
            for expected in (2,0):
                ident=s.begin(self.c);self.assertEqual(s.finish(ident,self.c,{'namespace':'boot','coverage_valid':True,'pcs':['0x1','0x2','0x1']}),expected)
            self.assertEqual(len(s.corpus()),1);s.close()
    def test_invalid_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d);ident=s.begin(self.c)
            self.assertEqual(s.finish(ident,self.c,{'namespace':'boot','coverage_valid':True,'saturated':True,'pcs':['0x1']}),0)
            self.assertEqual(s.corpus(),[]);s.close()
    def test_recover(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d);s.begin(self.c);s.recover();self.assertEqual(s.db.execute('SELECT status FROM runs').fetchone()[0],'interrupted');s.close()
if __name__=='__main__': unittest.main()
