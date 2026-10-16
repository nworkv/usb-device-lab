"""Model/topology are real. No device, UDC or host connection is opened."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from usb_device_lab.model import DeviceConfig
from usb_device_lab.mutator import Mutator,WireMutator
from usb_device_lab.mutation_graph import targets,at,digest
from usb_device_lab import uart_checkpoint as cp


def seed():
    return DeviceConfig(dict(schema_version=1,udc_driver='dummy_udc',udc_device='dummy_udc.0',speed=2,
        descriptors=[dict(type=1,hex='12010002000000400df00300000100000001'),dict(type=2,hex='090209000001008032')],
        runtime=dict(configurations=[dict(value=1,interfaces=[dict(number=0,alternates=[dict(setting=0,endpoints=[
            dict(descriptor_hex='07058102400000',payload_hex='00010203',interval_ms=10,read_length=64)])])])],self_powered=False),
        protocol=dict(name='script',initial_state='start',rules=[dict(event='control',match=dict(bmRequestType=161,bRequest=1,wIndex=0),reply_hex='0000',echo=False),
            dict(event='out',address=1,capture='rx',send=[dict(address=129,hex='0102')])]),
        wire_mutator=dict(seed=17,probability=0.05),metadata=dict(family='fixture',profile='test')))

class StructuredTests(unittest.TestCase):
    def test_deterministic_and_parent_unchanged(self):
        parent=seed();before=parent.canonical();a=Mutator(7);b=Mutator(7)
        for _ in range(32):
            x,t=a.mutate(parent);y,u=b.mutate(parent)
            self.assertEqual(x.canonical(),y.canonical());self.assertEqual(t,u);x.validate()
        self.assertEqual(parent.canonical(),before)
    def test_prng_restore_repeats_next_mutation(self):
        parent=seed();a=Mutator(9);a.mutate(parent);state=a.random.getstate();x,t=a.mutate(parent)
        b=Mutator(0);b.random.setstate(state);y,u=b.mutate(parent)
        self.assertEqual(x.canonical(),y.canonical());self.assertEqual(t,u)
    def test_catalogue_includes_new_branches_excludes_service(self):
        paths=[r['path'] for r in Mutator().catalogue(seed())['targets']]
        for prefix in ('/speed','/descriptors','/runtime','/protocol','/wire_mutator'):self.assertTrue(any(p.startswith(prefix) for p in paths))
        for prefix in ('/schema_version','/udc_driver','/udc_device','/metadata','/protocol/name'):self.assertFalse(any(p.startswith(prefix) for p in paths))
    def test_forced_payload_protocol_and_runtime_fields(self):
        paths=[('runtime','configurations',0,'interfaces',0,'alternates',0,'endpoints',0,'payload_hex'),
               ('runtime','configurations',0,'interfaces',0,'alternates',0,'endpoints',0,'descriptor_hex'),
               ('protocol','rules',0,'match','bRequest'),('protocol','rules',0,'echo'),('wire_mutator','probability')]
        for path in paths:
            parent=seed();child,trace=Mutator(4).mutate(parent,steps=1,target=path)
            self.assertNotEqual(at(parent.data,path),at(child.data,path),str(path));child.validate()
            entry=trace[0];self.assertEqual(entry['status'],'accepted')
            self.assertEqual(entry['before_sha256'],digest(at(parent.data,path)))
            self.assertEqual(entry['after_sha256'],digest(at(child.data,path)))
    def test_speed_repairs_runtime_packet_size_with_trace(self):
        child,trace=Mutator(5).mutate(seed(),steps=1,target=('speed',))
        self.assertEqual(child.data['speed'],3)
        ep=child.data['runtime']['configurations'][0]['interfaces'][0]['alternates'][0]['endpoints'][0]
        self.assertEqual(int.from_bytes(bytes.fromhex(ep['descriptor_hex'])[4:6],'little'),512)
        self.assertTrue(trace[0]['dependent_changes'])
    def test_impossible_required_alternate_is_explicit_skip(self):
        path=('runtime','configurations',0,'interfaces',0,'alternates',0,'setting')
        child,trace=Mutator(12).mutate(seed(),steps=1,target=path)
        self.assertEqual(at(child.data,path),0);self.assertEqual(trace[0]['status'],'skipped');self.assertIn('reason',trace[0])
    def test_bounds_and_locked_target(self):
        for steps in (0,17,True):
            with self.assertRaises(ValueError):Mutator().mutate(seed(),steps=steps)
        with self.assertRaises(ValueError):Mutator().mutate(seed(),target=('udc_driver',))
        nested=0
        for _ in range(34):nested={'x':nested}
        with self.assertRaises(ValueError):targets({'protocol':nested})
    def test_wire_mutator_sequence_unchanged_by_restore(self):
        a=WireMutator(dict(seed=12,probability=1));b=WireMutator(dict(seed=12,probability=1))
        self.assertEqual([a.apply('control',b'abcdef') for _ in range(50)],[b.apply('control',b'abcdef') for _ in range(50)])
    def test_v1_checkpoint_rejected_and_v2_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'checkpoint.json';cp.write(p,'f'*64,2,random.Random(1),'a'*32)
            self.assertEqual(cp.load(p,'f'*64,5)['next_iteration'],2)
            data=json.loads(p.read_text());data['version']='uart-round-robin-v1';p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'mutation engine'):cp.load(p,'f'*64,5)

if __name__=='__main__':unittest.main()
