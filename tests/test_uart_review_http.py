import http.client
import json
import shutil
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab.uart_review_http import Server, SCRIPT, review

ID, EXPORT, TOKEN = 'a'*32, 'evidence-'+'b'*32, 'review-http-'+'x'*40

class Supervisor:
    config = SimpleNamespace()
    def status(self):
        return {'state':'idle'}

class UARTReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(Supervisor(), TOKEN, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.01})
        self.thread.start()
        self.addCleanup(self.close)
        self.values = {'summary.json':{'capture_id':ID},'analysis.json':{'capture_id':ID},'complete.json':{'capture_id':ID}}
        def read(config, campaign, area, name):
            return json.dumps(self.values[name]).encode()
        self.reader = patch('usb_device_lab.uart_review_http.artifact', side_effect=read)
        self.render = patch('usb_device_lab.uart_review_http.render', return_value=b'<html>review fixture</html>')
        self.reader_mock = self.reader.start()
        self.render_mock = self.render.start()
        self.addCleanup(self.reader.stop)
        self.addCleanup(self.render.stop)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, path, auth=True, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        opts = {'Authorization':'Bearer '+TOKEN} if auth else {}
        opts.update(headers or {})
        try:
            connection.request('GET', path, headers=opts)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def url(self):
        return '/api/review?campaign='+ID+'&evidence='+EXPORT

    def test_authorized_attachment_uses_generated_bytes(self):
        code, data, headers = self.request(self.url())
        self.assertEqual(code, 200)
        self.assertEqual(data, b'<html>review fixture</html>')
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(self.reader_mock.call_count, 3)
        self.render_mock.assert_called_once_with(self.values['summary.json'],self.values['analysis.json'],self.values['complete.json'])

    def test_auth_and_origin_fail_before_read(self):
        self.assertIn(self.request(self.url(), auth=False)[0], (401,403))
        self.assertEqual(self.request(self.url(), headers={'Origin':'http://evil.example'})[0],403)
        self.reader_mock.assert_not_called()

    def test_traversal_duplicate_and_token_query_rejected(self):
        for path in (self.url().replace(EXPORT,'../outside'), self.url()+'&campaign='+ID, self.url()+'&token=secret'):
            self.assertEqual(self.request(path)[0],400)
        self.reader_mock.assert_not_called()

    def test_capture_identity_bound_to_query(self):
        self.values['summary.json']['capture_id']='c'*32
        self.assertEqual(self.request(self.url())[0],400)
        self.render_mock.assert_not_called()

    def test_missing_export_and_limits(self):
        self.reader_mock.side_effect=FileNotFoundError('missing')
        self.assertEqual(self.request(self.url())[0],404)
        self.reader_mock.side_effect=lambda *args:b'x'*20
        with patch('usb_device_lab.uart_review_http.MAX_INPUT',10):
            self.assertEqual(self.request(self.url())[0],413)

    def test_incomplete_export_semantics_are_renderer_responsibility(self):
        self.render_mock.side_effect=ValueError('Completed evidence export required')
        self.assertEqual(self.request(self.url())[0],400)

    def test_existing_page_gets_addon_and_old_status_route_survives(self):
        code, data, _=self.request('/',auth=False)
        self.assertEqual(code,200)
        self.assertIn(b'/uart-review.js',data)
        self.assertEqual(self.request('/uart-review.js',auth=False)[1],SCRIPT)
        self.assertEqual(json.loads(self.request('/api/state')[1])['state'],'idle')

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_addon_download_and_logout_in_simulated_dom(self):
        harness = """const vm=require('vm'),assert=require('assert');const nodes=new Map(),calls=[],downloads=[];
function node(tag){return{tag,value:'',children:[],handlers:{},disabled:false,append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},addEventListener(k,f){this.handlers[k]=f},click(){downloads.push(this.download)},remove(){}};}
nodes.set('campaign',node('select'));nodes.get('campaign').value='a'.repeat(32);nodes.set('logout',node('button'));nodes.set('error',node('p'));
const body=node('body'),ctx={token:'private-token',epoch:0,abort:{signal:{}},el:id=>nodes.get(id),window:{rows:[{id:'a'.repeat(32),evidence:['evidence-'+'b'.repeat(32)]}]},document:{body,createElement:node},URLSearchParams,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setTimeout(){},setInterval(){},fetch:async(url,opts)=>{calls.push([url,opts]);return{ok:true,blob:async()=>({})}},console};
vm.createContext(ctx);vm.runInContext(SOURCE,ctx);
(async()=>{const box=body.children[0],select=box.children[1],button=box.children[2];assert(!button.disabled);await button.handlers.click();assert.equal(calls[0][1].headers.Authorization,'Bearer private-token');assert(!calls[0][0].includes('private-token'));assert.equal(downloads.length,1);ctx.token='';nodes.get('logout').handlers.click();assert.equal(select.children.length,0);assert(button.disabled);})().catch(e=>{console.error(e);process.exitCode=1;});"""
        script=harness.replace('SOURCE',json.dumps(SCRIPT.decode('utf-8')))
        result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_addon_javascript_syntax_and_no_token_url(self):
        result=subprocess.run([shutil.which('node'),'--check'],input=SCRIPT,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(b'innerHTML',SCRIPT)
        self.assertNotIn(b'localStorage',SCRIPT)
        self.assertIn(b"Authorization:'Bearer '+token",SCRIPT)

if __name__=='__main__':unittest.main()
