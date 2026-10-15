import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.uart_pipeline import select_seeds, prepare_seeds, analyze_capture, records, run_complete

class UARTPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.seeds=self.root/'seeds';self.seeds.mkdir()
        self.manifest=self.root/'manifest.toml'
        self.manifest.write_text('[corpus]\nformat=1\nseed_dir="seeds"\n[[family]]\nname="hid"\nprofiles=["keyboard","mouse"]\n')
        seed=dict(schema_version=1,udc_driver='dummy_udc',udc_device='dummy_udc.0',descriptors=[dict(type=1,hex='12010002000000400df00300000100000001'),dict(type=2,hex='090209000001008032')],runtime={'endpoints':[]})
        for name in ('keyboard','mouse'):(self.seeds/('hid-'+name+'.json')).write_text(json.dumps(seed))
        self.config=SimpleNamespace(manifest=self.manifest,seed_dir=self.seeds,families=(),profiles=(),corpus_out=self.root/'corpus',udc='test.udc',gadget_udc_driver='test-driver',results_dir=self.root/'results',uart=SimpleNamespace())

    def test_manifest_order_and_selectors(self):
        self.assertEqual([r['profile'] for r in select_seeds(self.config)],['hid/keyboard','hid/mouse'])
        self.config.profiles=('mouse',)
        self.assertEqual(select_seeds(self.config)[0]['profile'],'hid/mouse')
        self.config.profiles=('hid/keyboard',)
        self.assertEqual(select_seeds(self.config)[0]['profile'],'hid/keyboard')
        self.config.families=('unknown',)
        with self.assertRaises(ValueError):select_seeds(self.config)

    def test_missing_seed_and_escaping_link(self):
        path=self.seeds/'hid-keyboard.json';path.unlink()
        with self.assertRaises(FileNotFoundError):select_seeds(self.config)
        outside=self.root/'outside.json';outside.write_text('{}');path.symlink_to(outside)
        with self.assertRaises(ValueError):select_seeds(self.config)

    def capture(self,parts):
        ident='a'*32;root=self.root/'results'/ident;capture=self.root/'logs'/ident
        root.mkdir(parents=True);capture.mkdir(parents=True)
        attempt=root/'attempt-000000';attempt.mkdir()
        (attempt/'config.json').write_bytes((self.seeds/'hid-keyboard.json').read_bytes())
        (attempt/'window.json').write_text(json.dumps(dict(attempt_id=attempt.name,capture_id=ident,start_ns=0,end_ns=100000,active_start_ns=0,active_end_ns=100000)))
        (attempt/'result.json').write_text(json.dumps(dict(infrastructure_error=None,executor_returncode=-15)))
        raw=b'';chunks=[]
        for sequence,data in enumerate(parts,1):
            start=len(raw);raw+=data
            chunks.append(dict(sequence=sequence,offset_start=start,offset_end=len(raw),byte_count=len(data),monotonic_ns=sequence,connection_epoch=1))
        (capture/'uart.raw').write_bytes(raw)
        (capture/'chunks.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in chunks))
        (capture/'events.jsonl').write_text('')
        return root,capture

    def test_post_capture_analysis_and_candidate_archive(self):
        root,capture=self.capture([b'context\nBUG: synthetic\n frame\n'])
        result=analyze_capture(self.config,root,capture,[])
        self.assertTrue(result['summary']['analysis_complete'])
        self.assertEqual(result['summary']['kernel_candidate_count'],1)
        self.assertEqual(len(list((self.config.corpus_out/'uart-candidates').glob('*.json'))),1)
        self.assertFalse(result['summary']['causal'])

    def test_overall_capture_larger_than_legacy_limit(self):
        parts=[b'x'*65535+b'\n']*270
        parts[-1]=b'BUG: late\n frame\n'
        root,capture=self.capture(parts)
        result=analyze_capture(self.config,root,capture,[])
        self.assertGreater(result['summary']['indexed_bytes'],16*1024*1024)
        self.assertGreater(result['summary']['batches'],1)
        self.assertEqual(result['summary']['kernel_candidate_count'],1)

    def test_invalid_index_no_complete_marker(self):
        root,capture=self.capture([b'BUG: fixture\n'])
        (capture/'chunks.jsonl').write_text('{"sequence":99}\n')
        with self.assertRaises(ValueError):analyze_capture(self.config,root,capture,[])
        output=next(root.glob('pipeline-*'))
        self.assertTrue((output/'analysis-error.json').exists())
        self.assertFalse((output/'complete.json').exists())

    def test_prepared_seed_snapshots_and_mutation_detection(self):
        selection=select_seeds(self.config)
        paths=prepare_seeds(self.config,selection)
        self.assertEqual(paths,prepare_seeds(self.config,selection))
        self.assertEqual(json.loads(Path(paths[0]).read_text())['udc_device'],'test.udc')
        (self.seeds/'hid-keyboard.json').write_text('{}')
        with self.assertRaises(ValueError):prepare_seeds(self.config,selection)

    def test_resume_forwarding_and_automatic_analysis_after_stop(self):
        import sys,types
        from unittest.mock import Mock
        root,capture=self.capture([b'BUG: fixture\n'])
        calls=[]
        class Reader:
            def __init__(self,settings,directory):pass
            def status(self):return {'state':'stopped'}
        core=types.ModuleType('usb_device_lab.uart_campaign')
        def execute(config,seeds,**kwargs):
            calls.append((seeds,kwargs['resume']))
            kwargs['receiver_factory'](config.uart,capture)
            return {'campaign_id':capture.name,'directory':str(root),'outcomes':[]}
        core.run_campaign=execute
        receiver=types.ModuleType('usb_device_lab.uart_receiver');receiver.UARTReceiver=Reader
        pre=types.ModuleType('usb_device_lab.uart_preflight');pre.inspect=Mock(return_value={'metadata_checks_passed':True})
        with patch.dict(sys.modules,{core.__name__:core,receiver.__name__:receiver,pre.__name__:pre}):
            result=run_complete(self.config,resume='checkpoint.json',preflight=False,lock_root=self.root/'locks')
        self.assertEqual(calls[0][1],'checkpoint.json')
        self.assertTrue(all('uart-seeds-' in path for path in calls[0][0]))
        self.assertTrue(result['analysis']['summary']['analysis_complete'])
        self.assertTrue((root/'selection.json').exists())

    def test_partial_final_jsonl_record_excluded(self):
        path=self.root/'events.jsonl';path.write_bytes(b'{"kind":"started"}\n{"kind":')
        self.assertEqual(list(records(path)),[{'kind':'started'}])

if __name__=='__main__':unittest.main()
