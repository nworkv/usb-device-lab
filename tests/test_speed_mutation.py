"""Low-speed mutation dependencies and mutation-engine checkpoint compatibility."""
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest
from usb_device_lab.model import DeviceConfig
from usb_device_lab.mutator import Mutator
from usb_device_lab.mutation_graph import edit, speed_choices, digest
from usb_device_lab import uart_checkpoint as checkpoint


def parent(speed=2, transfer=3, size=64, interval=1):
    endpoint=bytes((7,5,129,transfer,size&255,size>>8,interval)).hex()
    return DeviceConfig(dict(schema_version=1,udc_driver='dummy_udc',udc_device='dummy_udc.0',speed=speed,
        descriptors=[dict(type=1,hex='12010002000000406b1d1001000100000001'),dict(type=2,hex='090209000001008032')],
        runtime=dict(endpoints=[dict(descriptor_hex=endpoint,payload_hex='00')]),protocol=dict(name='script',rules=[])))


class SpeedMutationTests(unittest.TestCase):
    def test_interrupt_parents_can_reach_low_and_high_speed(self):
        original=parent();before=original.canonical();observed=set()
        for seed in range(40):
            child,trace=Mutator(seed).mutate(original,steps=1,target=('speed',))
            child.validate();observed.add(child.data['speed'])
        self.assertTrue({1,3} <= observed)
        self.assertEqual(original.canonical(),before)

    def test_bulk_runtime_does_not_offer_low_speed(self):
        self.assertEqual(speed_choices(parent(transfer=2,interval=0).data),(2,3))
        child,trace=Mutator(5).mutate(parent(transfer=2,interval=0),steps=1,target=('speed',))
        self.assertEqual(child.data['speed'],3)
        raw=bytes.fromhex(child.data['runtime']['endpoints'][0]['descriptor_hex'])
        self.assertEqual(int.from_bytes(raw[4:6],'little'),512)

    def test_low_speed_repairs_packet_and_interval_with_trace(self):
        data=copy.deepcopy(parent().data)
        before=data['runtime']['endpoints'][0]['descriptor_hex']
        class SelectLow:
            def choice(self, values):
                if 1 not in values:raise AssertionError('Low Speed not offered')
                return 1
        trace=edit(data,('speed',),SelectLow())
        DeviceConfig(data)
        after=data['runtime']['endpoints'][0]['descriptor_hex'];raw=bytes.fromhex(after)
        self.assertEqual((raw[3]&3,int.from_bytes(raw[4:6],'little'),raw[6]),(3,8,10))
        self.assertEqual(trace['dependent_changes'],[dict(path='/runtime/endpoints/0/descriptor_hex',before_sha256=digest(before),after_sha256=digest(after))])

    def test_low_speed_endpoint_edits_stay_executable(self):
        path=('runtime','endpoints',0,'descriptor_hex')
        for seed in range(100):
            data=copy.deepcopy(parent(speed=1,size=8,interval=10).data)
            edit(data,path,random.Random(seed));DeviceConfig(data)
            raw=bytes.fromhex(data['runtime']['endpoints'][0]['descriptor_hex'])
            self.assertEqual(raw[3]&3,3)
            self.assertTrue(1<=int.from_bytes(raw[4:6],'little')<=8)
            self.assertGreaterEqual(raw[6],10)

    def test_restored_prng_produces_same_next_child_and_trace(self):
        value=parent();first=Mutator(17);first.mutate(value);state=first.random.getstate()
        child,trace=first.mutate(value)
        second=Mutator(0);second.random.setstate(state);repeated,repeated_trace=second.mutate(value)
        self.assertEqual(child.canonical(),repeated.canonical())
        self.assertEqual(trace,repeated_trace)
        self.assertEqual(Mutator.VERSION,'usb-tree-model-valid-v3')

    def test_v3_checkpoint_roundtrip_and_v2_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'checkpoint.json'
            checkpoint.write(path,'f'*64,1,random.Random(7),'a'*32)
            self.assertEqual(checkpoint.load(path,'f'*64,10)['next_iteration'],1)
            data=json.loads(path.read_text());data['version']='usb-tree-model-valid-v2-round-robin'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'mutation engine'):
                checkpoint.load(path,'f'*64,10)


if __name__=='__main__':
    unittest.main()
