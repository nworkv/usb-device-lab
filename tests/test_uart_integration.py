"""Linux PTY integration. Only the USB executor command and lock root are replaced."""
import dataclasses
import http.client
import json
import os
import pty
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.uart_config import load
from usb_device_lab.uart_campaign import run_campaign
from usb_device_lab.uart_receiver import UARTReceiver
from usb_device_lab.uart_evidence import export_campaign
from usb_device_lab.uart_checkpoint import load as load_checkpoint, fingerprint
from usb_device_lab.model import DeviceConfig
from usb_device_lab.uart_web import Server, Supervisor

TOKEN = 'integration-' + 'x' * 40
PAYLOAD = b'context\r\n[ 1.0] BUG: KASAN: synthetic \xd0\x90\nCall Trace:\n frame\n---[ end trace fixture ]---\n'

@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux termios/PTYS required')
class UARTIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.master, self.slave = pty.openpty()
        self.writers, self.writer_errors, self.receivers, self.children = [], [], [], []
        self.server, self.server_thread, self.supervisor = None, None, None
        self.seed = self.root / 'seed.json'
        self.seed.write_text(json.dumps(dict(schema_version=1, udc_driver='dummy_udc', udc_device='dummy_udc.0',
            descriptors=[dict(type=1, hex='12010002000000400df00300000100000001'), dict(type=2, hex='090209000001008032')], runtime={'endpoints':[]})))
        self.config_path = self.root / 'uart.toml'
        self.configure(1)
        self.addCleanup(self.cleanup)

    def configure(self, iterations):
        self.config_path.write_text('[gadget]\nudc="test.udc"\nudc_driver="test-driver"\ndevice_config="seed.json"\n'
            '[telemetry]\nmode="uart"\n[uart]\nport='+json.dumps(os.ttyname(self.slave))+'\nread_timeout=0.02\nreconnect_delay=0.1\n'
            '[corpus]\nseed_dir="seeds"\nmanifest="manifest.toml"\n[campaign]\niterations='+str(iterations)+'\nseconds=1\nseed=7\n'
            'pre_run_quiet_seconds=0\npost_run_capture_seconds=0.05\n[run]\nresults_dir="results"\nlogs_dir="logs"\n')
        self.config = load(self.config_path)

    def cleanup(self):
        if self.supervisor:
            self.supervisor.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(3)
        for writer in self.writers:
            writer.join(5)
        for receiver in self.receivers:
            receiver.stop()
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
        os.close(self.master)
        os.close(self.slave)
        self.tmp.cleanup()

    def wait_for(self, condition, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError('Integration condition timed out')

    def receiver_factory(self, settings, directory):
        receiver = UARTReceiver(settings, directory)
        self.receivers.append(receiver)
        return receiver

    def command(self, path, cancel=None):
        ready = path.parent / 'fixture-ready'
        def writer():
            try:
                self.wait_for(ready.exists)
                count = self.receivers[-1].status()['bytes_received'] if self.receivers else 0
                os.write(self.master, PAYLOAD[:25])
                os.write(self.master, PAYLOAD[25:])
                if self.receivers:
                    self.wait_for(lambda: self.receivers[-1].status()['bytes_received'] >= count+len(PAYLOAD))
                else:
                    time.sleep(0.05)
                if cancel is not None:
                    cancel.set()
            except Exception as error:
                self.writer_errors.append(str(error))
        thread = threading.Thread(target=writer)
        self.writers.append(thread)
        thread.start()
        code = 'from pathlib import Path; import time; Path('+repr(str(ready))+').write_text("ready"); time.sleep(60)'
        return [sys.executable, '-u', '-c', code]

    def tracked_spawn(self):
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            self.children.append(child)
            return child
        return patch('usb_device_lab.uart_campaign.subprocess.Popen', side_effect=spawn)

    def request(self, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Authorization':'Bearer '+TOKEN, 'Content-Type':'application/json'}
        try:
            connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_http_start_real_capture_export_and_download(self):
        original = run_campaign
        def safe_run(config, seeds, **kwargs):
            def real_receiver(settings, directory):
                receiver = kwargs['receiver_factory'](settings, directory)
                self.receivers.append(receiver)
                return receiver
            return original(config, seeds, stop=kwargs['stop'], resume=kwargs['resume'],
                receiver_factory=real_receiver, command_factory=self.command, lock_root=self.root / 'locks')
        self.supervisor = Supervisor(self.config, [self.seed])
        self.server = Server(self.supervisor, TOKEN, 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.01})
        self.server_thread.start()
        with self.tracked_spawn(), patch('usb_device_lab.uart_campaign.run_campaign', side_effect=safe_run):
            self.assertEqual(self.request('/api/start', {})[0], 202)
            self.wait_for(lambda: self.supervisor.status()['state'] in ('completed','failed'), 10)
        self.assertEqual(self.supervisor.status()['state'], 'completed', self.supervisor.status())
        result = self.supervisor.status()['campaign']
        campaign = Path(result['directory'])
        capture = self.config.logs_dir / 'uart' / result['campaign_id']
        self.assertEqual((capture / 'uart.raw').read_bytes(), PAYLOAD)
        output = export_campaign(campaign, self.config.logs_dir / 'uart')
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['kernel_candidate_count'], 1)
        self.assertEqual(summary['attempts'][0]['candidate_reports'], ['report-000000'])
        report = json.loads((output / 'report-000000.json').read_text())
        self.assertIn('synthetic А', report['text'])
        self.assertFalse(report['causal'])
        path = '/api/file?campaign='+result['campaign_id']+'&area='+output.name+'&name=summary.json'
        code, data, headers = self.request(path)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(data), summary)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertTrue(all(p.poll() is not None for p in self.children))
        self.assertEqual(self.writer_errors, [])

    def test_cancel_and_resume_real_receiver_and_mutator(self):
        self.configure(2)
        stop = threading.Event()
        def command(path):
            return self.command(path, stop if path.parent.name == 'attempt-000001' else None)
        with self.tracked_spawn():
            first = run_campaign(self.config, [self.seed], stop=stop, receiver_factory=self.receiver_factory,
                command_factory=command, lock_root=self.root / 'locks')
            old = Path(first['directory'])
            self.assertEqual(json.loads((old / 'checkpoint.json').read_text())['next_iteration'], 1)
            second = run_campaign(self.config, [self.seed], resume=old / 'checkpoint.json',
                receiver_factory=self.receiver_factory, command_factory=self.command, lock_root=self.root / 'locks')
        new = Path(second['directory'])
        self.assertNotEqual(first['campaign_id'], second['campaign_id'])
        self.assertEqual((old / 'attempt-000001/config.json').read_bytes(), (new / 'attempt-000001/config.json').read_bytes())
        self.assertEqual([x['iteration'] for x in second['outcomes']], [1])
        self.assertEqual(json.loads((new / 'checkpoint.json').read_text())['next_iteration'], 2)
        for result in (first, second):
            output = export_campaign(Path(result['directory']), self.config.logs_dir / 'uart')
            self.assertEqual(json.loads((output / 'summary.json').read_text())['capture_id'], result['campaign_id'])
        self.assertTrue(all(p.poll() is not None for p in self.children))
        self.assertEqual(self.writer_errors, [])

if __name__ == '__main__':
    unittest.main()
