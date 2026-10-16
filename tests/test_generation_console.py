"""Generation-controller lifecycle tests; builders and USB execution are isolated."""
from dataclasses import dataclass
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from usb_device_lab import generation_console as console


@dataclass(frozen=True)
class Config:
    corpus_out: Path
    seed_dir: Path
    manifest: Path
    families: tuple = ('hid',)
    profiles: tuple = ()


class GenerationConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Config(self.root/'corpus',self.root/'source',self.root/'source.toml')
        self.s = console.Supervisor.__new__(console.Supervisor)
        self.s.config,self.s.lock,self.s.thread,self.s.closing = self.config,threading.RLock(),None,False
        self.s.generation_worker,self.s.generation,self.s.generated = None,dict(state='idle',id=None),None
        self.s.corpus_family = None

    def fake_build(self, seed_dir, manifest, output, count, seed):
        output = Path(output)
        output.mkdir()
        (output/'seeds').mkdir()
        (output/'manifest.toml').write_text('[corpus]'+chr(10)+'format=1'+chr(10)+'[[family]]'+chr(10)+'name="hid-keyboard"'+chr(10)+'profiles=['+','.join('"v%d"'%i for i in range(count))+']'+chr(10))
        result = dict(configurations=count,source_profiles=1,per_profile=count)
        (output/'complete.json').write_text(json.dumps(result))
        return result

    def run_build(self, builder=None):
        with patch.object(console,'source_profiles',return_value=[('hid-keyboard',object())]),patch.object(console,'build_variants',side_effect=builder or self.fake_build):
            self.s.start_build(dict(preset='standard',count=3,seed=42))
            self.s.generation_worker.join(3)
            self.assertFalse(self.s.generation_worker.is_alive())

    def test_complete_build_can_activate_without_modifying_base(self):
        self.run_build()
        self.assertEqual(self.s.generation['state'],'completed')
        ident = self.s.generation['id']
        self.s.activate(ident)
        active = self.s.corpus_config()
        self.assertEqual(active.families,('hid-keyboard',))
        self.assertEqual(active.profiles,())
        self.assertNotEqual(active.seed_dir,self.config.seed_dir)
        self.assertEqual(self.s.config,self.config)
        self.s.activate(None)
        self.assertIs(self.s.corpus_config(),self.config)

    def test_failed_build_cannot_activate(self):
        def fail(*args,**kwargs):raise OSError('fixture disk failure')
        self.run_build(fail)
        self.assertEqual(self.s.generation['state'],'failed')
        with self.assertRaises(ValueError):self.s.activate(self.s.generation['id'])

    def test_wrong_or_missing_marker_cannot_publish(self):
        def mismatch(*args,**kwargs):
            result = self.fake_build(*args,**kwargs)
            (Path(args[2])/'complete.json').write_text('{"configurations":1}')
            return result
        self.run_build(mismatch)
        self.assertEqual(self.s.generation['state'],'failed')

    def test_generation_blocks_start_switch_and_false_stop(self):
        started,release = threading.Event(),threading.Event()
        def wait(*args,**kwargs):
            started.set()
            if not release.wait(2):raise RuntimeError('fixture timeout')
            return self.fake_build(*args,**kwargs)
        with patch.object(console,'source_profiles',return_value=[('hid-keyboard',object())]),patch.object(console,'build_variants',side_effect=wait):
            self.s.start_build(dict(preset='standard',count=3,seed=42))
            try:
                self.assertTrue(started.wait(1))
                self.assertTrue(self.s.busy())
                with self.assertRaises(RuntimeError):self.s.start_build(dict(preset='standard',count=1,seed=1))
                with self.assertRaises(RuntimeError):self.s.activate(None)
                with self.assertRaises(RuntimeError):self.s.stop()
            finally:
                release.set()
                self.s.generation_worker.join(3)

    def test_invalid_parameters_and_arbitrary_paths_rejected(self):
        for value in ({},dict(preset='standard',count=True,seed=1),dict(preset='standard',count=51,seed=1),dict(preset='standard',count=1,seed=True),dict(preset='shell',count=1,seed=1),dict(preset='standard',count=1,seed=1,output='/tmp/unapproved')):
            with self.assertRaises(ValueError):console.parameters(value)
        self.assertFalse((self.config.corpus_out/'generated').exists())

    def test_unauthenticated_build_has_no_effect(self):
        handler = console.Handler.__new__(console.Handler)
        handler.path = '/api/corpus-build'
        handler.guard = Mock(return_value=False)
        handler.server = SimpleNamespace(supervisor=Mock())
        handler.do_POST()
        handler.server.supervisor.start_build.assert_not_called()

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_javascript_syntax(self):
        result = subprocess.run(['node','--check'],input=console.SCRIPT,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())


    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_served_scripts_share_compatible_scope(self):
        from usb_device_lab.research_ui import SCRIPT as research
        from usb_device_lab.event_ui import SCRIPT as events
        from usb_device_lab.skip_console import SKIP_SCRIPT as controls
        result = subprocess.run(['node','--check'],input=research+events+controls+console.SCRIPT,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())

if __name__=='__main__':
    unittest.main()
