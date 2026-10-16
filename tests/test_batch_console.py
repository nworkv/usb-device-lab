"""Preview binding and batch UI adapters; no stand connections."""
from dataclasses import dataclass, replace
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from usb_device_lab import batch_console as ui
from usb_device_lab import batch_campaign as engine


@dataclass(frozen=True)
class Config:
    path: Path
    results_dir: Path
    families: tuple = ('hid',)
    profiles: tuple = ()
    telemetry_mode: str = 'uart'
    udc: str = 'fixture.udc'
    seconds: float = 0.1
    pre_run_quiet_seconds: float = 0
    post_run_capture_seconds: float = 0


class BatchConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        self.config=Config(root/'lab.toml',root/'results')
        self.s=ui.Supervisor.__new__(ui.Supervisor)
        self.s.config,self.s.lock,self.s.thread,self.s.closing=self.config,threading.RLock(),None,False
        self.s.generated,self.s.options,self.s.corpus_family=None,{},None
        self.s.batch,self.s.batch_preview,self.s.batch_display=None,None,False
        self.s.stop_event,self.s.skip_event=threading.Event(),threading.Event()
        self.groups=[dict(family='hid',selection=[dict(digest='a'*64,path='/fixture/seed.json')])]

    def preview(self):
        with patch.object(ui,'make_plan',return_value=self.groups):return self.s.plan_batch(True)

    def test_preview_is_read_only_and_all_groups_is_explicit(self):
        with patch.object(ui,'make_plan',return_value=self.groups) as make:
            result=self.s.plan_batch(True)
        self.assertEqual(result['planned_attempts'],1)
        self.assertEqual(make.call_args.args[0].families,())
        self.assertEqual(self.config.families,('hid',))
        self.assertIsNone(self.s.thread)
        self.assertFalse(self.config.results_dir.exists())
        with self.assertRaises(ValueError):self.s.plan_batch(1)

    def test_start_consumes_preview_and_freezes_plan(self):
        preview=self.preview()
        with patch.object(ui,'load',return_value=self.config),patch.object(ui.threading,'Thread') as worker:
            self.s.start_batch(preview['token'])
        self.assertIsNone(self.s.batch_preview)
        worker.return_value.start.assert_called_once_with()
        self.assertEqual(worker.call_args.kwargs['args'][1],self.groups)
        self.assertTrue(self.s.batch_display)

    def test_changed_memory_settings_reject_before_thread(self):
        preview=self.preview();self.s.config=replace(self.config,seconds=0.2)
        with patch.object(ui.threading,'Thread') as worker,self.assertRaisesRegex(ValueError,'Настройки изменились'):
            self.s.start_batch(preview['token'])
        worker.assert_not_called()

    def test_changed_disk_config_rejects_before_thread(self):
        preview=self.preview()
        with patch.object(ui,'load',return_value=replace(self.config,seconds=0.3)),patch.object(ui.threading,'Thread') as worker,self.assertRaises(ValueError):
            self.s.start_batch(preview['token'])
        worker.assert_not_called()

    def test_worker_forwards_capture_stop_skip_and_expected_plan(self):
        capture=Mock();receiver=Mock()
        def pipeline(config,**kwargs):
            kwargs['on_capture'](receiver,Path('/fixture/'+'b'*32));kwargs['on_stage']('running');return {}
        def run(config,**kwargs):
            self.assertEqual(kwargs['expected_plan'],self.groups)
            kwargs['pipeline'](config,on_capture=capture,stop=self.s.stop_event,skip=self.s.skip_event,expected_selection=self.groups[0]['selection'])
            return dict(state='completed',planned_attempts=1,groups=[],completed_groups=1,error=None)
        with patch.object(ui,'run_batch',side_effect=run),patch.object(ui,'run_complete',side_effect=pipeline) as execute:
            self.s.batch_work(self.config,self.groups)
        self.assertEqual(self.s.active,'b'*32)
        self.assertIs(self.s.receiver,receiver)
        self.assertIs(execute.call_args.kwargs['stop'],self.s.stop_event)
        self.assertIs(execute.call_args.kwargs['skip'],self.s.skip_event)
        capture.assert_called_once()
        self.assertEqual(self.s.stage,'completed')

    def test_unauthenticated_start_has_no_effect(self):
        handler=ui.Handler.__new__(ui.Handler);handler.path='/api/batch-start'
        handler.guard=Mock(return_value=False);handler.server=SimpleNamespace(supervisor=Mock())
        handler.do_POST();handler.server.supervisor.start_batch.assert_not_called()

    def test_changed_global_plan_rejected_before_output_or_pipeline(self):
        execute=Mock()
        with patch.object(engine,'make_plan',return_value=[dict(family='changed',selection=[])]),self.assertRaisesRegex(ValueError,'Batch plan changed'):
            engine.run_batch(self.config,pipeline=execute,expected_plan=self.groups)
        execute.assert_not_called()
        self.assertFalse(self.config.results_dir.exists())

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_javascript_syntax(self):
        result=subprocess.run(['node','--check'],input=ui.SCRIPT,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_complete_served_script_scope(self):
        from usb_device_lab.research_ui import SCRIPT as research
        from usb_device_lab.event_ui import SCRIPT as events
        from usb_device_lab.skip_console import SKIP_SCRIPT as controls
        from usb_device_lab.generation_console import SCRIPT as generation
        result=subprocess.run(['node','--check'],input=research+events+controls+generation+ui.SCRIPT,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())


if __name__=='__main__':
    unittest.main()
