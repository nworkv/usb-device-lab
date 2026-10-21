"""Batch records group errors and continues; operator Stop still wins."""
import json
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from usb_device_lab import batch_campaign as batch

@dataclass
class Config:
    results_dir: Path
    families: tuple = ()
    iterations: int = 1
    telemetry_mode: str = 'uart'
    udc: str = 'test'
    seconds: float = 1
    pre_run_quiet_seconds: float = 0
    post_run_capture_seconds: float = 0

class BatchContinueTests(unittest.TestCase):
    def run_case(self, behavior, stop=None):
        groups=[{'family':name,'selection':[{'path':name}]} for name in ('one','two')]
        calls=[]
        def pipeline(config, **kwargs):
            calls.append(config.families[0])
            ident=('a' if len(calls)==1 else 'b')*32
            kwargs['on_capture'](None,Path(ident))
            result={'campaign_id':ident,'outcomes':[{}],'analysis':{'summary':{'analysis_complete':True,'capture_id':ident,'kernel_candidate_count':0}}}
            if len(calls)==1:behavior(result,kwargs)
            return result
        with tempfile.TemporaryDirectory() as tmp,patch.object(batch,'make_plan',return_value=groups):
            progress=batch.run_batch(Config(Path(tmp)),pipeline=pipeline,stop=stop)
            persisted=json.loads((Path(progress['directory'])/'progress.json').read_text())
            self.assertEqual(persisted,progress)
        return calls,progress

    def test_finding_does_not_stop_next_group(self):
        calls,p=self.run_case(lambda r,k:r['analysis']['summary'].update(kernel_candidate_count=2))
        self.assertEqual(calls,['one','two']);self.assertEqual(p['state'],'completed');self.assertEqual(p['kernel_candidate_count'],2);self.assertTrue(p['review_required'])

    def test_reported_errors_do_not_stop_next_group(self):
        for field in ('execution_error','analysis_error'):
            with self.subTest(field=field):
                calls,p=self.run_case(lambda r,k:r.update({field:'probe failure'}))
                self.assertEqual(calls,['one','two']);self.assertEqual(p['failed_groups'],1);self.assertEqual(p['groups'][0]['error'],'probe failure')

    def test_exception_does_not_stop_next_group(self):
        def fail(r,k):raise RuntimeError('receiver failure')
        calls,p=self.run_case(fail)
        self.assertEqual(calls,['one','two']);self.assertEqual(p['state'],'completed');self.assertEqual(p['failed_groups'],1)

    def test_incomplete_analysis_remains_failed_not_zero_findings(self):
        calls,p=self.run_case(lambda r,k:r.pop('analysis'))
        self.assertEqual(calls,['one','two']);self.assertEqual(p['groups'][0]['state'],'failed');self.assertIsNone(p['groups'][0]['kernel_candidate_count'])

    def test_operator_stop_prevents_next_group(self):
        stop=threading.Event()
        calls,p=self.run_case(lambda r,k:stop.set(),stop)
        self.assertEqual(calls,['one']);self.assertEqual(p['state'],'stopped')

    def test_partial_group_is_recorded_as_failed(self):
        calls,p=self.run_case(lambda r,k:r.update(outcomes=[]))
        self.assertEqual(calls,['one','two']);self.assertEqual(p['failed_groups'],1);self.assertEqual(p['groups'][0]['attempts_recorded'],0)

if __name__=='__main__':unittest.main()
