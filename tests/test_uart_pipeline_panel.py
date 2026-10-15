import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from usb_device_lab.uart_pipeline_web import Supervisor,details,pipeline_file
from usb_device_lab.uart_pipeline_ui import SCRIPT

ID='a'*32
AREA='pipeline-'+'b'*32
class UARTPipelinePanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=SimpleNamespace(path=self.root/'lab.toml',results_dir=self.root/'results',logs_dir=self.root/'logs',iterations=2,seconds=1,seed=7,families=('hid',),profiles=(),uart=SimpleNamespace(port='/dev/test'))
        self.campaign=self.config.results_dir/ID;self.campaign.mkdir(parents=True)
        (self.campaign/'campaign.json').write_text(json.dumps({'capture_id':ID,'mode':'uart'}))
        (self.campaign/'checkpoint.json').write_text('{"next_iteration":1}')
        self.area=self.campaign/AREA;self.area.mkdir()
        (self.area/'pipeline-summary.json').write_text('{"analysis_complete":true,"kernel_candidate_count":1}')
        with sqlite3.connect(self.area/'candidates.sqlite3') as db:
            db.executescript('CREATE TABLE reports(epoch INTEGER,start INTEGER,end INTEGER,attempt TEXT,payload TEXT);CREATE TABLE diagnostics(kind TEXT,attempt TEXT,payload TEXT);')
            db.execute('INSERT INTO reports VALUES (?,?,?,?,?)',(1,0,10,'attempt-000000',json.dumps({'text':'BUG: synthetic','context':'ctx','associations':[]})))
            db.execute('INSERT INTO diagnostics VALUES (?,?,?)',('uart_capture',None,'{"loss":"unknown"}'))

    def test_history_ledger_and_progress(self):
        data=details(self.config,ID)
        self.assertEqual(data['pipelines'][0]['reports'][0]['text'],'BUG: synthetic')
        self.assertEqual(data['pipelines'][0]['diagnostics'][0]['kind'],'uart_capture')
        supervisor=Supervisor(self.config);supervisor.active=ID
        self.assertEqual(supervisor.status()['completed'],1)

    def test_pipeline_files_are_allowlisted_and_nofollow(self):
        self.assertGreater(pipeline_file(self.config,ID,AREA,'candidates.sqlite3',metadata_only=True),0)
        with self.assertRaises(ValueError):pipeline_file(self.config,ID,AREA,'../secret')
        target=self.root/'outside';target.write_text('secret')
        path=self.area/'reports.jsonl';path.symlink_to(target)
        with self.assertRaises(OSError):pipeline_file(self.config,ID,AREA,'reports.jsonl')

    def modules(self,passed=True):
        loader=types.ModuleType('usb_device_lab.uart_config');loader.load=lambda p:self.config
        pipe=types.ModuleType('usb_device_lab.uart_pipeline')
        pipe.select_seeds=Mock(return_value=[{'path':'seed.json','profile':'hid/keyboard'}])
        pipe.prepare_seeds=Mock(return_value=['snapshot.json'])
        pipe.dump=Mock()
        pipe.analyze_capture=Mock(return_value={'directory':str(self.area),'summary':{'analysis_complete':True}})
        pre=types.ModuleType('usb_device_lab.uart_preflight');pre.inspect=Mock(return_value={'metadata_checks_passed':passed})
        receiver=types.ModuleType('usb_device_lab.uart_receiver')
        class Reader:
            def __init__(self,*a):pass
            def status(self):return {'state':'stopped'}
        receiver.UARTReceiver=Reader
        core=types.ModuleType('usb_device_lab.uart_campaign')
        def execute(config,seeds,**kwargs):
            self.assertEqual(seeds,['snapshot.json'])
            kwargs['receiver_factory'](config.uart,config.logs_dir/'uart'/ID)
            return {'campaign_id':ID,'outcomes':[]}
        core.run_campaign=Mock(side_effect=execute)
        return {m.__name__:m for m in (loader,pipe,pre,receiver,core)},pipe,core

    def test_start_resume_use_snapshots_and_automatic_analysis(self):
        modules,pipe,core=self.modules()
        supervisor=Supervisor(self.config)
        with patch.dict(sys.modules,modules):
            supervisor.start(ID);supervisor.thread.join(2)
            self.assertFalse(supervisor.thread.is_alive())
            self.assertEqual(supervisor.status()['stage'],'completed')
            self.assertEqual(core.run_campaign.call_args.kwargs['resume'],self.campaign/'checkpoint.json')
            pipe.analyze_capture.assert_called_once()
        supervisor.close()

    def test_preflight_failure_never_runs_core(self):
        modules,pipe,core=self.modules(False)
        supervisor=Supervisor(self.config)
        with patch.dict(sys.modules,modules):
            supervisor.start();supervisor.thread.join(2)
            self.assertEqual(supervisor.status()['stage'],'failed')
            core.run_campaign.assert_not_called();pipe.prepare_seeds.assert_not_called()
        supervisor.close()

    def test_analysis_failure_separate_from_execution(self):
        modules,pipe,core=self.modules()
        pipe.analyze_capture.side_effect=ValueError('bad index')
        supervisor=Supervisor(self.config)
        with patch.dict(sys.modules,modules):
            supervisor.start();supervisor.thread.join(2)
            state=supervisor.status()
            self.assertEqual(state['stage'],'failed')
            self.assertEqual(state['analysis_error'],'bad index')
            self.assertIsNone(state['execution_error'])
        supervisor.close()

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_ui_syntax_no_html_injection_or_token_storage(self):
        result=subprocess.run([shutil.which('node'),'--check'],input=SCRIPT,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(b'innerHTML',SCRIPT)
        self.assertNotIn(b'localStorage',SCRIPT)
        self.assertIn(b'AbortController',SCRIPT)

if __name__=='__main__':unittest.main()
