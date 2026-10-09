import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.workspace import WorkspaceServer
from usb_device_lab.workspace_ui import PAGE, SCRIPT

TOKEN = 'operator-test-' + 'x'*40
CAMPAIGN, RUN = 'a'*32, 'b'*32


class Supervisor:
    def __init__(self, config):
        self.config=config;self._lock=threading.RLock();self._last=None;self._thread=None;self.started=[]
    def status(self):return {'state':'idle','runs':0,'error':None}
    def start(self):self.started.append(self.config.iterations)
    def stop(self):pass
    def resume(self):pass


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.path=self.root/'lab.toml';self.path.write_text('fixture')
        self.config=SimpleNamespace(path=self.path,results_dir=self.root,iterations=1000,seconds=2.0,seed=42,families=(),profiles=())
        loader=patch('usb_device_lab.workspace.load',side_effect=lambda path:self.config)
        loader.start();self.addCleanup(loader.stop)
        self.supervisor=Supervisor(self.config)
        self.server=WorkspaceServer(self.supervisor,TOKEN,self.root,0,config=self.config)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01});self.thread.start();self.addCleanup(self.close)
        with sqlite3.connect(self.root/'runs.sqlite3') as db:
            db.executescript('CREATE TABLE runs(id TEXT,status TEXT,started REAL,result TEXT);CREATE TABLE campaign_attempts(campaign_id TEXT,iteration INTEGER,run_id TEXT);')
            result={'errors':[{'kind':'cleanup','summary':'fixture failure'}],'verdict':{'outcome':'infrastructure_failure'},'kernel_events':[]}
            db.execute('INSERT INTO runs VALUES (?,?,?,?)',(RUN,'error',1.0,json.dumps(result)))
            db.execute('INSERT INTO campaign_attempts VALUES (?,?,?)',(CAMPAIGN,0,RUN))
        directory=self.root/'runs'/RUN;directory.mkdir(parents=True)
        (directory/'executor.log').write_text('one\ntwo\n')
        (directory/'config.json').write_text('{"schema_version":1}')
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def request(self,path,body=None,auth=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+TOKEN
        try:
            connection.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
            response=connection.getresponse();return response.status,response.read(),dict(response.getheaders())
        finally:connection.close()
    def test_empty_start_uses_config_and_rejects_user_override(self):
        self.assertEqual(self.request('/api/workspace/start',{})[0],202)
        self.assertEqual(self.supervisor.started,[1000])
        self.assertEqual(self.request('/api/workspace/start',{'iterations':100})[0],400)
        self.assertEqual(self.supervisor.started,[1000])
    def test_status_reports_config_parameters(self):
        code,body,_=self.request('/api/workspace/status');self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['configured']['iterations'],1000)
        self.assertEqual(json.loads(body)['configured']['seed'],42)
    def test_errors_visible_and_raw_result_not_returned(self):
        code,body,_=self.request('/api/workspace/runs?campaign='+CAMPAIGN);self.assertEqual(code,200)
        data=json.loads(body);self.assertEqual(len(data['errors']),1)
        self.assertEqual(data['errors'][0]['errors'][0]['kind'],'cleanup')
        self.assertNotIn('result',data['rows'][0])
    def test_tail_and_full_download_are_distinct(self):
        query='campaign='+CAMPAIGN+'&run='+RUN
        code,body,_=self.request('/api/workspace/tail?'+query+'&source=executor');self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['text'],'one\ntwo\n')
        code,body,headers=self.request('/api/workspace/file?'+query+'&name=config.json');self.assertEqual(code,200)
        self.assertEqual(body,b'{"schema_version":1}')
        self.assertIn('attachment',headers['Content-Disposition'])
    def test_membership_authentication_and_traversal(self):
        query='campaign='+CAMPAIGN+'&run='+RUN+'&name=config.json'
        self.assertEqual(self.request('/api/workspace/file?'+query,auth=False)[0],401)
        self.assertEqual(self.request('/api/workspace/file?'+query.replace(CAMPAIGN,'c'*32))[0],404)
        self.assertEqual(self.request('/api/workspace/file?'+query.replace('config.json','../lab.toml'))[0],400)
    def test_configuration_reload_changes_new_budget(self):
        self.config=SimpleNamespace(**dict(vars(self.config),iterations=100))
        self.assertEqual(self.request('/api/workspace/start',{})[0],202)
        self.assertEqual(self.supervisor.started,[100])

    def test_legacy_database_without_tracking_table_is_readable(self):
        with sqlite3.connect(self.root/'runs.sqlite3') as db:
            db.execute('DROP TABLE campaign_attempts')
        code,body,_=self.request('/api/workspace/runs?campaign=legacy');self.assertEqual(code,200)
        self.assertEqual(len(json.loads(body)['errors']),1)

    def test_single_page_has_no_user_budget_fields_or_persistent_token(self):
        self.assertNotIn(b'id="iterations"',PAGE);self.assertNotIn(b'id="seconds"',PAGE);self.assertNotIn(b'id="seed"',PAGE)
        self.assertNotIn(b'localStorage',SCRIPT);self.assertNotIn(b'sessionStorage',SCRIPT)
        self.assertNotIn(b'innerHTML',SCRIPT);self.assertIn(b'3000',SCRIPT)
        self.assertIn(b'AbortController',SCRIPT)


if __name__=='__main__':unittest.main()
