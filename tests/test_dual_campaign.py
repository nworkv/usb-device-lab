"""Shared engine lifecycle tests with fake model/receiver and real child processes."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from usb_device_lab.dual_campaign import run_campaign
from usb_device_lab.dual_config import from_dict
from usb_device_lab.uart_receiver import CaptureJournal


def config(root, mode):
    raw=dict(gadget=dict(udc='test.udc',udc_driver='test-driver',device_config=str(root/'seed.json')),
        telemetry=dict(mode=mode,schema_version=1),uart=dict(port='/dev/fixture'),
        ssh=dict(host='10.10.10.208',user='operator',identity_file=str(root/'key'),known_hosts_file=str(root/'known_hosts')),
        corpus=dict(seed_dir=str(root/'seeds'),manifest=str(root/'manifest.toml')),
        campaign=dict(iterations=2,seconds=0.06,seed=42,feedback='none',pre_run_quiet_seconds=0,post_run_capture_seconds=0),
        run=dict(corpus_out=str(root/'corpus'),results_dir=str(root/'results'),logs_dir=str(root/'logs')))
    return from_dict(raw,root/'lab.toml')

class Device:
    def __init__(self,data):self.data=json.loads(json.dumps(data)); self.digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    @classmethod
    def load(cls,path):return cls(json.loads(Path(path).read_text()))

class Mutator:
    def __init__(self,seed):self.random=random.Random(seed)
    def mutate(self,parent):
        value=dict(parent.data); value['fixture_random']=self.random.randrange(100000)
        return Device(value),['fixture-only']

class Receiver:
    def __init__(self,settings,path,fail=False):self.path=Path(path); self.state='new'; self.stop_event=threading.Event(); self.fail=fail
    def status(self):return dict(state=self.state,completeness='unknown')
    def stop(self):self.stop_event.set()
    def run(self):
        journal=CaptureJournal(self.path)
        try:
            if self.fail:self.state='failed'; raise RuntimeError('fixture auth failure')
            self.state='connected'; journal.event('connected',1)
            journal.append(b'fixture console\n',1)
            self.stop_event.wait(); self.state='stopped'; journal.event('stopped',1)
        finally:journal.close()

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.seed=self.root/'seed.json'; self.seed.write_text('{"fixture":true}')
        model=types.ModuleType('usb_device_lab.model'); model.DeviceConfig=Device
        mutator=types.ModuleType('usb_device_lab.mutator'); mutator.Mutator=Mutator
        self.modules={'usb_device_lab.model':model,'usb_device_lab.mutator':mutator}

    def run_mode(self,mode,**kw):
        c=config(self.root,mode)
        with patch.dict(sys.modules,self.modules):
            return run_campaign(c,[str(self.seed)],receiver_factory=lambda settings,path:Receiver(settings,path),
                command_factory=kw.pop('command_factory',lambda _: [sys.executable,'-c','import time; time.sleep(60)']),
                lock_root=self.root/'locks',**kw)

    def test_uart_and_ssh_share_lifecycle_and_have_true_metadata(self):
        for mode in ('uart','ssh'):
            result=self.run_mode(mode)
            root=Path(result['directory']); meta=json.loads((root/'campaign.json').read_text())
            self.assertEqual(meta['mode'],mode)
            self.assertEqual(Path(meta['capture_directory']).parent.name,mode)
            self.assertEqual(json.loads((root/'checkpoint.json').read_text())['next_iteration'],2)
            self.assertTrue(all(row['window_completed'] for row in result['outcomes']))
            self.assertTrue(all(row['executor_returncode'] in (-15,-9) for row in result['outcomes']))

    def test_receiver_failure_prevents_usb_executor_for_both_modes(self):
        for mode in ('uart','ssh'):
            calls=[]; c=config(self.root,mode)
            with patch.dict(sys.modules,self.modules),self.assertRaises(RuntimeError):
                run_campaign(c,[str(self.seed)],receiver_factory=lambda settings,path:Receiver(settings,path,True),
                    command_factory=lambda path:calls.append(path),lock_root=self.root/'locks')
            self.assertEqual(calls,[])

    def test_interruption_and_resume_do_not_replay_completed_prefix(self):
        stop=threading.Event(); calls=[]
        def command(path):
            calls.append(path)
            if len(calls)==2:stop.set()
            return [sys.executable,'-c','import time; time.sleep(60)']
        first=self.run_mode('ssh',stop=stop,command_factory=command)
        checkpoint=Path(first['directory'])/'checkpoint.json'
        self.assertEqual(json.loads(checkpoint.read_text())['next_iteration'],1)
        resumed=self.run_mode('ssh',resume=checkpoint)
        self.assertEqual([row['iteration'] for row in resumed['outcomes']],[1])
        self.assertEqual(json.loads((Path(resumed['directory'])/'campaign.json').read_text())['first_iteration'],1)

    def test_cross_transport_checkpoint_is_rejected_before_capture(self):
        first=self.run_mode('uart')
        checkpoint=Path(first['directory'])/'checkpoint.json'
        with self.assertRaises(ValueError):self.run_mode('ssh',resume=checkpoint)

if __name__=='__main__':unittest.main()
