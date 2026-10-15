"""No host, SSH daemon or Raw Gadget needed. Real local pipes/journals and HTTP."""
from dataclasses import replace
import copy
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from usb_device_lab.dual_config import from_dict, SSHSettings, ssh_settings
from usb_device_lab.ssh_capture import SSHReceiver, command
from usb_device_lab.dual_web import Supervisor, Server, artifact


def fixture(root):
    return dict(gadget=dict(udc='test.udc', udc_driver='test-driver', device_config=str(root/'seed.json')),
        telemetry=dict(schema_version=1, mode='uart'),
        uart=dict(port='/dev/fixture', baudrate=115200),
        ssh=dict(host='10.10.10.208', user='operator', identity_file=str(root/'key'), known_hosts_file=str(root/'known_hosts')),
        corpus=dict(seed_dir=str(root/'seeds'), manifest=str(root/'manifest.toml')),
        campaign=dict(iterations=3, seconds=0.1, seed=42, feedback='none'),
        run=dict(corpus_out=str(root/'corpus'), results_dir=str(root/'results'), logs_dir=str(root/'logs')))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.raw=fixture(self.root)

    def test_both_modes_and_no_fake_uart_in_ssh_config(self):
        uart=from_dict(self.raw,self.root/'lab.toml')
        ssh=from_dict(self.raw,self.root/'lab.toml','ssh')
        self.assertEqual(uart.telemetry_mode,'uart'); self.assertEqual(uart.uart.port,'/dev/fixture')
        self.assertEqual(ssh.telemetry_mode,'ssh'); self.assertIsNone(ssh.uart)
        self.assertEqual(ssh.capture_settings.host,'10.10.10.208')

    def test_legacy_uart_config_remains_accepted(self):
        raw=copy.deepcopy(self.raw); raw.pop('ssh')
        self.assertEqual(from_dict(raw,self.root/'lab.toml').telemetry_mode,'uart')
        with self.assertRaises(ValueError):from_dict(raw,self.root/'lab.toml','ssh')

    def test_invalid_modes_commands_hosts_and_types_rejected(self):
        for value in ('auto', 'both', None):
            raw=copy.deepcopy(self.raw); raw['telemetry']['mode']=value
            with self.assertRaises(ValueError):from_dict(raw,self.root/'lab.toml')
        for field,value in [('host','-oProxyCommand=evil'),('host','host;echo bad'),('user','u;bad'),('port',True),('use_sudo','yes'),('read_timeout',float('nan')),('identity_file','/tmp/%h')]:
            raw=copy.deepcopy(self.raw); raw['ssh'][field]=value
            with self.assertRaises(ValueError):from_dict(raw,self.root/'lab.toml','ssh')
        raw=copy.deepcopy(self.raw); raw['ssh']['command']='reboot'
        with self.assertRaises(ValueError):from_dict(raw,self.root/'lab.toml','ssh')


class SSHCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.settings=SSHSettings(host='10.10.10.208',user='operator',identity_file='/tmp/key',known_hosts_file='/tmp/known_hosts',lease_seconds=600)

    def factory(self,code):
        def create(argv,**kwargs):
            self.argv=argv
            self.process=subprocess.Popen([sys.executable,'-u','-c',code],**kwargs)
            return self.process
        return create

    def test_options_do_not_disable_host_key_verification(self):
        argv=command(self.settings)
        self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertIn('BatchMode=yes',argv)
        self.assertIn('ControlPath=none',argv)
        self.assertIn('timeout --signal=TERM --kill-after=2 600',argv[-1])
        self.assertNotIn('StrictHostKeyChecking=no',argv)
        self.assertIn('sudo -n dmesg --follow-new --raw',argv[-1])

    def test_capture_ready_index_and_clean_stop(self):
        code="import os,sys; os.write(2,b'USB_DEVICE_LAB_READY_V1'+bytes([10])); os.write(1,b'BUG: synthetic'+bytes([10])); sys.stdin.buffer.read()"
        r=SSHReceiver(self.settings,self.root/'capture',self.factory(code))
        errors=[]
        def work():
            try:r.run()
            except Exception as e:errors.append(str(e))
        worker=threading.Thread(target=work); worker.start()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and (r.status()['state']!='connected' or not r.status()['bytes_received']):time.sleep(0.01)
        self.assertEqual(r.status()['state'],'connected'); r.stop(); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertEqual(errors,[])
        self.assertIsNotNone(self.process.poll())
        self.assertEqual((self.root/'capture/uart.raw').read_bytes(),b'BUG: synthetic\n')
        chunks=[json.loads(v) for v in (self.root/'capture/chunks.jsonl').read_text().splitlines()]
        self.assertEqual(chunks[0]['offset_end'],len(b'BUG: synthetic\n'))
        self.assertEqual(chunks[0]['connection_epoch'],1)
        self.assertEqual((self.root/'capture/uart.raw').stat().st_mode&0o777,0o600)
        self.assertEqual(r.status()['state'],'stopped')

    def test_auth_failure_never_becomes_connected(self):
        r=SSHReceiver(self.settings,self.root/'capture',self.factory("import sys; print('Permission denied',file=sys.stderr); sys.exit(255)"))
        with self.assertRaises(RuntimeError):r.run()
        self.assertEqual(r.status()['state'],'failed')
        self.assertIsNotNone(self.process.poll())
        self.assertEqual((self.root/'capture/uart.raw').read_bytes(),b'')

    def test_ready_stream_exit_is_failure_not_success_or_fallback(self):
        code="import os,sys; os.write(2,b'USB_DEVICE_LAB_READY_V1'+bytes([10])); sys.exit(1)"
        r=SSHReceiver(self.settings,self.root/'capture',self.factory(code))
        with self.assertRaises(RuntimeError):r.run()
        self.assertEqual(r.status()['state'],'failed')

    def test_stderr_is_bounded_and_kept_out_of_kernel_capture(self):
        r=SSHReceiver(self.settings,self.root/'capture',self.factory("import sys; sys.stderr.write('x'*200000); sys.stderr.flush(); sys.exit(255)"))
        with self.assertRaises(RuntimeError):r.run()
        self.assertLessEqual((self.root/'capture/ssh-stderr.log').stat().st_size,16384)
        self.assertEqual((self.root/'capture/uart.raw').read_bytes(),b'')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.config=from_dict(fixture(self.root),self.root/'lab.toml')
        self.ident='a'*32
        campaign=self.config.results_dir/self.ident; campaign.mkdir(parents=True)
        (campaign/'campaign.json').write_text(json.dumps(dict(mode='uart',capture_id=self.ident)))
        (campaign/'checkpoint.json').write_text('{"next_iteration":1}')
        self.supervisor=Supervisor(self.config)
        self.server=Server(self.supervisor,'t'*40,0)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01}); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3); self.supervisor.close()

    def request(self,path,body=None,token=True,extra=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Authorization':'Bearer '+'t'*40} if token else {}
        if extra:headers.update(extra)
        if body is not None:headers['Content-Type']='application/json'
        try:
            c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
            r=c.getresponse(); return r.status,r.read()
        finally:c.close()

    def test_auth_origin_history_and_download(self):
        self.assertEqual(self.request('/api/state',token=False)[0],401)
        self.assertEqual(self.request('/api/state',extra={'Origin':'https://evil.invalid'})[0],403)
        status,data=self.request('/api/history'); self.assertEqual(status,200)
        self.assertEqual(json.loads(data)[0]['mode'],'uart')
        status,data=self.request('/api/file?campaign='+self.ident+'&area=campaign&name=campaign.json')
        self.assertEqual(status,200); self.assertEqual(json.loads(data)['capture_id'],self.ident)

    def test_mode_selection_uses_loader_and_refuses_while_busy(self):
        ssh=from_dict(fixture(self.root),self.root/'lab.toml','ssh')
        with patch('usb_device_lab.dual_web.load',return_value=ssh):
            self.assertEqual(self.request('/api/mode',{'mode':'ssh'})[0],200)
        self.assertEqual(self.supervisor.config.telemetry_mode,'ssh')
        with patch.object(self.supervisor,'busy',return_value=True):
            self.assertEqual(self.request('/api/mode',{'mode':'uart'})[0],409)
        self.assertEqual(self.supervisor.config.telemetry_mode,'ssh')

    def test_cross_transport_resume_and_path_traversal_rejected(self):
        self.supervisor.config=from_dict(fixture(self.root),self.root/'lab.toml','ssh')
        with patch('usb_device_lab.dual_web.load',return_value=self.supervisor.config):
            self.assertEqual(self.request('/api/resume',{'campaign':self.ident})[0],400)
        self.assertEqual(self.request('/api/file?campaign='+self.ident+'&area=campaign&name=../secret')[0],400)
        (self.config.results_dir/self.ident/'selection.json').symlink_to(self.root/'outside')
        with self.assertRaises(OSError):artifact(self.config,self.ident,'campaign','selection.json')

if __name__=='__main__':unittest.main()
