"""Unlimited transport configuration, real timeout/local fake-host cleanup, strict resume."""
from dataclasses import dataclass,replace
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from usb_device_lab.dual_config import SSHSettings,ssh_settings
from usb_device_lab.ssh_capture import command,SSHReceiver
from usb_device_lab.ssh_lifetime import restore
from usb_device_lab import uart_checkpoint as cp

@dataclass(frozen=True)
class Config:
    ssh: SSHSettings
    telemetry_mode: str='ssh'
    iterations: int=10000
    host_guard: str='same-target'

def fingerprint(config,parents):
    return hashlib.sha256(json.dumps(dict(settings=vars(config.ssh),iterations=config.iterations,
        guard=config.host_guard,parents=parents),sort_keys=True).encode()).hexdigest()

class UnlimitedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.values=dict(host='10.10.10.208',user='nikita',identity_file='/tmp/key',known_hosts_file='/tmp/known_hosts')
        self.settings=SSHSettings(**self.values)
    def checkpoint(self,lease):
        config=Config(replace(self.settings,lease_seconds=lease))
        path=self.root/('checkpoint-'+str(lease)+'.json')
        cp.write(path,fingerprint(config,['parent']),166,random.Random(42),'a'*32)
        return path
    def test_defaults_zero_and_finite_option_preserved(self):
        self.assertEqual(self.settings.lease_seconds,0)
        self.assertEqual(ssh_settings(self.values).lease_seconds,0)
        for value in (0,30,600,3600):self.assertEqual(ssh_settings({**self.values,'lease_seconds':value}).lease_seconds,value)
        for value in (-1,True,1,3601,'0'):
            with self.assertRaises(ValueError):ssh_settings({**self.values,'lease_seconds':value})
    def test_remote_zero_disables_lifetime_without_weakening_ssh(self):
        argv=command(self.settings);remote=shlex.split(argv[-1])
        self.assertEqual(remote[:4],['timeout','--signal=TERM','--kill-after=2','0'])
        self.assertIn('StrictHostKeyChecking=yes',argv);self.assertIn('ServerAliveInterval=2',argv)
    def test_old_leases_resume_at_saved_iteration(self):
        for lease in (30,600,3600):
            path=self.checkpoint(lease);before=path.read_bytes()
            result=restore(path,Config(self.settings),['parent'],fingerprint)
            self.assertEqual(result['next_iteration'],166);self.assertEqual(result['ssh_lifetime_migrated_from'],lease)
            self.assertEqual(path.read_bytes(),before)
    def test_other_resume_changes_rejected(self):
        path=self.checkpoint(600)
        for config in (replace(Config(self.settings),iterations=9999),replace(Config(self.settings),host_guard='different-target')):
            with self.assertRaises(ValueError):restore(path,config,['parent'],fingerprint)
        with self.assertRaises(ValueError):restore(path,Config(self.settings),['different-parent'],fingerprint)
    def test_new_checkpoint_has_no_migration(self):
        path=self.checkpoint(0);result=restore(path,Config(self.settings),['parent'],fingerprint)
        self.assertEqual(result['next_iteration'],166);self.assertNotIn('ssh_lifetime_migrated_from',result)
    def test_corrupt_and_duplicate_checkpoint_rejected(self):
        path=self.checkpoint(600);path.write_text('{"fingerprint":"a","fingerprint":"b"}')
        with self.assertRaises(ValueError):restore(path,Config(self.settings),['parent'],fingerprint)
    @unittest.skipUnless(shutil.which('timeout'),'GNU timeout required')
    def test_real_zero_timeout_and_clean_remote_wrapper_stop(self):
        binary=self.root/'bin';binary.mkdir()
        dmesg=binary/'dmesg'
        dmesg.write_text('#!'+sys.executable+'\nimport sys,time\nif "--help" in sys.argv: print("--follow-new")\nelif "--follow-new" in sys.argv:\n print("fixture log",flush=True)\n time.sleep(60)\n')
        dmesg.chmod(0o700)
        env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''))
        processes=[]
        def factory(argv,**kwargs):
            process=subprocess.Popen(shlex.split(argv[-1]),env=env,**kwargs);processes.append(process);return process
        receiver=SSHReceiver(replace(self.settings,use_sudo=False),self.root/'capture',factory)
        errors=[]
        def run():
            try:receiver.run()
            except Exception as error:errors.append(str(error))
        worker=threading.Thread(target=run);worker.start()
        try:
            deadline=time.monotonic()+4
            while time.monotonic()<deadline and receiver.status()['state']!='connected':time.sleep(0.01)
            self.assertEqual(receiver.status()['state'],'connected')
            time.sleep(0.2);self.assertEqual(receiver.status()['state'],'connected')
        finally:receiver.stop();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(receiver.status()['state'],'stopped');self.assertIsNotNone(processes[0].poll())
    def test_finite_124_has_explicit_reason(self):
        def factory(argv,**kwargs):return subprocess.Popen([sys.executable,'-c','import sys;sys.exit(124)'],**kwargs)
        receiver=SSHReceiver(replace(self.settings,lease_seconds=30),self.root/'expired',factory)
        with self.assertRaisesRegex(RuntimeError,'lifetime expired'):receiver.run()
        self.assertEqual(receiver.status()['failure_kind'],'lifetime_expired')

if __name__=='__main__':unittest.main()
