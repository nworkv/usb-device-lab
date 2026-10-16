"""Batch orchestration uses isolated selectors/pipeline and temporary output only."""
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from usb_device_lab import batch_campaign as batch
from usb_device_lab import dual_pipeline


@dataclass(frozen=True)
class Config:
    results_dir: Path
    families: tuple = ()
    profiles: tuple = ()
    iterations: int = 999
    seconds: float = 0.1
    pre_run_quiet_seconds: float = 0
    post_run_capture_seconds: float = 0
    telemetry_mode: str = 'uart'
    udc: str = 'fixture.udc'


class BatchTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.config=Config(self.root/'results');self.calls=[]
        self.families=[dict(name='group%d'%i,count=50) for i in range(3)]

    def select(self, config):
        family=config.families[0]
        return [dict(family=family,profile=family+'/v%d'%i,path='/fixture/'+family+'-%d.json'%i,digest=('%064x'%i)) for i in range(50)]

    def pipeline(self, config, **kwargs):
        self.calls.append((config,kwargs))
        self.assertEqual(config.iterations,len(kwargs['expected_selection']))
        return dict(campaign_id='a'*32,outcomes=[{} for _ in range(config.iterations)],analysis=dict(summary=dict(kernel_candidate_count=0)))

    def run_batch(self, pipeline=None, stop=None):
        with patch.object(batch,'catalogue',return_value=dict(families=self.families)),patch.object(batch,'select_seeds',side_effect=self.select):
            return batch.run_batch(self.config,pipeline=pipeline or self.pipeline,stop=stop)

    def test_more_than_128_seeds_execute_as_sequential_bounded_groups(self):
        result=self.run_batch()
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['planned_attempts'],150)
        self.assertEqual(result['completed_groups'],3)
        self.assertEqual([c.iterations for c,k in self.calls],[50,50,50])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(json.loads((Path(result['directory'])/'progress.json').read_text()),result)

    def test_stop_prevents_next_group(self):
        stop=threading.Event()
        def execute(config,**kwargs):
            result=self.pipeline(config,**kwargs);stop.set();return result
        result=self.run_batch(execute,stop)
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(len(self.calls),1)

    def test_infrastructure_failure_prevents_next_group(self):
        def execute(config,**kwargs):
            result=self.pipeline(config,**kwargs);result['analysis_error']='fixture analysis failure';return result
        result=self.run_batch(execute)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(len(self.calls),1)
        self.assertIn('fixture analysis failure',result['error'])

    def test_kernel_candidates_require_review_without_claiming_causality(self):
        def execute(config,**kwargs):
            result=self.pipeline(config,**kwargs);result['analysis']['summary']['kernel_candidate_count']=1;return result
        result=self.run_batch(execute)
        self.assertEqual(result['state'],'review_required')
        self.assertEqual(len(self.calls),1)
        self.assertFalse(result['causal'])

    def test_skipped_attempts_are_not_reported_as_usb_windows(self):
        def execute(config,**kwargs):
            result=self.pipeline(config,**kwargs);result['outcomes'][0]['skipped']=True;return result
        result=self.run_batch(execute)
        self.assertEqual(result['state'],'completed')
        self.assertEqual([row['skipped_attempts'] for row in result['groups']],[1,1,1])

    def test_already_stopped_batch_does_not_create_output(self):
        stop=threading.Event();stop.set()
        result=self.run_batch(stop=stop)
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(self.calls,[])
        self.assertFalse(self.config.results_dir.exists())

    def test_batch_lock_prevents_pipeline_execution(self):
        parent=self.config.results_dir/'batches';parent.mkdir(parents=True,mode=0o700)
        with (parent/'batch.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.run_batch()
        self.assertEqual(self.calls,[])

    def test_unknown_selector_errors_are_not_silently_skipped(self):
        from dataclasses import replace
        config=replace(self.config,profiles=('unknown',))
        with patch.object(batch,'catalogue',return_value=dict(families=self.families)),patch.object(batch,'select_seeds',side_effect=ValueError('Unknown family/profile selector')):
            with self.assertRaisesRegex(ValueError,'Unknown family/profile'):
                batch.make_plan(config)

    def test_changed_selection_rejected_before_preflight_or_execution(self):
        with patch.object(dual_pipeline,'select_seeds',return_value=[dict(digest='changed')]),patch.object(dual_pipeline,'inspect') as inspect,patch.object(dual_pipeline,'prepare_seeds') as prepare,patch.object(dual_pipeline,'run_campaign') as execute:
            with self.assertRaisesRegex(ValueError,'changed after batch planning'):
                dual_pipeline.run_complete(self.config,expected_selection=[dict(digest='original')])
        inspect.assert_not_called();prepare.assert_not_called();execute.assert_not_called()


if __name__=='__main__':
    unittest.main()
