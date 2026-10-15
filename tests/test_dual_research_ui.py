"""Frontend contracts and file previews; no USB/SSH campaign is executed."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from usb_device_lab.research_frontend import Server
from usb_device_lab.research_files import preview
from usb_device_lab.research_ui import PAGE,STYLE,SCRIPT

class ResearchUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.config=SimpleNamespace(results_dir=root/'results',logs_dir=root/'logs');self.ident='a'*32
        campaign=self.config.results_dir/self.ident;campaign.mkdir(parents=True)
        (campaign/'campaign.json').write_text(json.dumps(dict(mode='ssh',capture_id=self.ident)))
        self.capture=self.config.logs_dir/'ssh'/self.ident;self.capture.mkdir(parents=True)
        (self.capture/'uart.raw').write_bytes(b'normal\nBUG: fixture\nWARNING: fixture\n')
        self.supervisor=Mock();self.supervisor.config=self.config;self.supervisor.status.return_value={'stage':'idle'}
        self.server=Server(self.supervisor,'x'*40,0)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01});self.thread.start();self.addCleanup(self.close)
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def get(self,path,token=False,origin=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Authorization':'Bearer '+'x'*40} if token else {}
        if origin:headers['Origin']=origin
        try:
            client.request('GET',path,headers=headers);response=client.getresponse()
            return response.status,response.read(),response.getheader('Content-Type')
        finally:client.close()
    def test_root_and_external_assets(self):
        for path,payload,kind in [('/',PAGE,'text/html'),('/research.js',SCRIPT,'text/javascript'),('/research.css',STYLE,'text/css')]:
            code,data,mime=self.get(path);self.assertEqual(code,200);self.assertEqual(data,payload);self.assertIn(kind,mime)
        self.assertEqual(self.get('/research.js?token=bad')[0],400)
    def test_api_auth_and_origin_still_required(self):
        self.assertEqual(self.get('/api/state')[0],401)
        self.assertEqual(self.get('/api/state',token=True)[0],200)
        self.assertEqual(self.get('/',origin='https://evil.invalid')[0],403)
    def test_preview_is_bounded_literal_and_preserves_original(self):
        before=(self.capture/'uart.raw').read_bytes()
        value=preview(self.config,self.ident,'capture','uart.raw',level='critical')
        self.assertEqual(len(value['rows']),1);self.assertEqual(value['rows'][0]['start'],7)
        self.assertEqual(value['raw_window_sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(value['search_scope'],'loaded_window');self.assertEqual((self.capture/'uart.raw').read_bytes(),before)
        self.assertEqual(preview(self.config,self.ident,'capture','uart.raw',query='.*')['rows'],[])
        with self.assertRaises(ValueError):preview(self.config,self.ident,'capture','uart.raw',limit=262145)
    def test_ui_has_safe_text_and_replay_snapshot_contracts(self):
        for forbidden in (b'innerHTML',b'localStorage',b'sessionStorage'):self.assertNotIn(forbidden,SCRIPT)
        for required in (b'AbortController',b'/api/replay',b'/api/replay-info',b'replay(a.id,id)',b'JSON.stringify(log,null,2)',b'lastActive',b'setInterval(refresh,2000)'):self.assertIn(required,SCRIPT)
        self.assertIn(b'data-tab="results"',PAGE);self.assertIn(b'backdrop-filter',STYLE)
    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_javascript_syntax(self):
        result=subprocess.run([shutil.which('node'),'--check'],input=SCRIPT,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
