"""Persistent SQLite/chunk/window tests with fixture file readers, no hardware."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.event_registry import Registry

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.cfg=SimpleNamespace(results_dir=self.root/'results',logs_dir=self.root/'logs');self.id='a'*32
        self.camp=self.cfg.results_dir/self.id;self.camp.mkdir(parents=True)
        self.cap=self.cfg.logs_dir/'ssh'/self.id;self.cap.mkdir(parents=True)
        self.write(self.camp/'campaign.json',dict(capture_id=self.id,mode='ssh'))
        self.registry=Registry(self.cfg)
        self.p1=patch('usb_device_lab.event_registry.open_file',side_effect=self.open);self.p1.start();self.addCleanup(self.p1.stop)
        self.p2=patch('usb_device_lab.event_registry.read_json',side_effect=self.read);self.p2.start();self.addCleanup(self.p2.stop)
    def write(self,path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    def location(self,cfg,campaign,area,name):
        return cfg.logs_dir/'ssh'/campaign/name if area=='capture' else cfg.results_dir/campaign/name if area=='campaign' else cfg.results_dir/campaign/area/name
    def open(self,*args):return self.location(*args).open('rb')
    def read(self,*args):return json.loads(self.location(*args).read_text())
    def capture(self,parts):
        raw=b'';rows=[]
        for seq,(data,mono) in enumerate(parts,1):
            start=len(raw);raw+=data
            rows.append(dict(sequence=seq,offset_start=start,offset_end=len(raw),byte_count=len(data),monotonic_ns=mono,connection_epoch=1))
        (self.cap/'uart.raw').write_bytes(raw)
        (self.cap/'chunks.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    def attempt(self,name,start,end,error=None):
        self.write(self.camp/name/'window.json',dict(start_ns=start,end_ns=end))
        self.write(self.camp/name/'result.json',dict(infrastructure_error=error,executor_returncode=0))
    def test_old_event_survives_tail_and_registry_restart(self):
        self.capture([(b'Kernel panic - fixture\n'+b'INFO\n'*10000,100),(b'INFO\n'*10000,200)])
        self.attempt('attempt-000000',50,250);self.registry.tick(self.id)
        first=self.registry.page(self.id)['events'][0]
        self.assertEqual(first['kind'],'panic');self.assertEqual(first['start'],0)
        again=Registry(self.cfg).page(self.id)['events'][0]
        self.assertEqual(first['id'],again['id']);self.assertEqual(again['attempts'][0]['attempt'],'attempt-000000')
        self.registry.tick(self.id);self.assertEqual(self.registry.page(self.id)['count'],1)
    def test_split_header_keeps_start_and_shows_ambiguity(self):
        self.capture([(b'Kernel pa',100),(b'nic - fixture\n',200)])
        self.attempt('attempt-000000',50,150);self.attempt('attempt-000001',150,250)
        self.registry.tick(self.id);row=self.registry.page(self.id)['events'][0]
        self.assertEqual(row['monotonic_ns'],100);self.assertEqual(row['monotonic_end_ns'],200)
        self.assertTrue(row['ambiguous']);self.assertEqual(len(row['attempts']),2)
    def test_executor_error_exact_relation(self):
        self.capture([(b'normal\n',100)]);self.attempt('attempt-000000',50,150,'executor failed')
        self.registry.tick(self.id);row=self.registry.page(self.id)['events'][0]
        self.assertEqual(row['kind'],'executor_error');self.assertEqual(row['attempts'][0]['relation'],'executor_result')
    def test_filters_pagination_and_stable_ids(self):
        self.capture([(b'BUG: x\nOops: x\nWARNING: x\n',100)]);self.attempt('attempt-000000',50,150)
        self.registry.tick(self.id);page=self.registry.page(self.id,limit=1)
        self.assertIsNotNone(page['next_before']);self.assertEqual(len(self.registry.page(self.id,kind='bug')['events']),1)
        self.assertEqual(len(self.registry.page(self.id,before=page['next_before'])['events']),2)
    def test_repeat_link_survives_restart(self):
        self.capture([(b'BUG: x\n',100)]);self.attempt('attempt-000000',50,150);self.registry.tick(self.id)
        event=self.registry.page(self.id)['events'][0]['id'];repeat='b'*32
        self.write(self.cfg.results_dir/repeat/'campaign.json',dict(capture_id=repeat,mode='ssh'))
        self.write(self.cfg.results_dir/repeat/'replay-source.json',dict(event_ref=dict(campaign=self.id,event=event)))
        Registry(self.cfg).tick(repeat)
        self.assertEqual(Registry(self.cfg).detail(self.id,event)['repeats'],[repeat])
    def test_truncation_fails_closed_and_long_line_is_bounded(self):
        self.capture([(b'BUG: '+b'x'*50000,100),(b'x'*50000+b'\n',200)])
        self.registry.tick(self.id)
        with self.registry.session() as db:
            state=json.loads(db.execute('SELECT payload FROM progress WHERE campaign=?',(self.id,)).fetchone()[0])
            self.assertLessEqual(len(bytes.fromhex(state['carry'])),8192)
        (self.cap/'uart.raw').write_bytes(b'')
        with self.assertRaises(ValueError):self.registry.tick(self.id)

if __name__=='__main__':unittest.main()
