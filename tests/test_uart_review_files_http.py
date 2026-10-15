"""Real files -> production artifact reader/decoder/renderer -> HTTP attachment."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from usb_device_lab.uart_review_http import Server

ID, EXPORT, TOKEN = 'a'*32, 'evidence-'+'b'*32, 'review-files-'+'x'*40

class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags=[]
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

class UARTReviewFilesHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=SimpleNamespace(results_dir=self.root/'results',logs_dir=self.root/'logs')
        self.directory=self.config.results_dir/ID/EXPORT
        self.directory.mkdir(parents=True)
        self.summary=dict(schema_version=1,capture_id=ID,causal=False,completeness='unknown',indexed_bytes=42,unindexed_tail_bytes=0,
            attempts=[dict(attempt_id='attempt-000000',window_available=True,result_available=True,candidate_reports=['report-000000'])],diagnostics=[])
        self.analysis=dict(capture_id=ID,causal=False,reports=[dict(report_id='report-000000',causal=False,association='candidate',
            offset_start=0,offset_end=42,connection_epoch=1,termination='capture_end',truncated=True,
            associations=[dict(attempt_id='attempt-000000',receive_phases=['active'])],context='context <img src=x onerror=alert(1)>',
            text='BUG: synthetic А\n frame\n</pre><script>bad</script>')])
        self.marker=dict(schema_version=1,capture_id=ID,export_complete=True)
        self.write('summary.json',self.summary)
        self.write('analysis.json',self.analysis)
        self.write('complete.json',self.marker)
        (self.directory/'review.html').write_text('<script>UNTRUSTED_STORED_HTML</script>')
        supervisor=SimpleNamespace(config=self.config,status=lambda:{'state':'idle'})
        self.server=Server(supervisor,TOKEN,0)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01})
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def write(self,name,value):
        (self.directory/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

    def request(self,auth=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            headers={'Authorization':'Bearer '+TOKEN} if auth else {}
            connection.request('GET','/api/review?campaign='+ID+'&evidence='+EXPORT,headers=headers)
            response=connection.getresponse()
            return response.status,response.read(),dict(response.getheaders())
        finally:
            connection.close()

    def test_real_json_renders_escaped_generated_attachment(self):
        original={p.name:p.read_bytes() for p in self.directory.iterdir()}
        code,data,headers=self.request()
        self.assertEqual(code,200)
        self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertIn('attachment',headers['Content-Disposition'])
        page=data.decode('utf-8')
        self.assertIn('BUG: synthetic А\n frame',page)
        self.assertIn('Предшествующий контекст',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('UNTRUSTED_STORED_HTML',page)
        parser=Tags();parser.feed(page)
        self.assertFalse({'script','img','iframe','object'} & set(parser.tags))
        self.assertEqual({p.name:p.read_bytes() for p in self.directory.iterdir()},original)

    def test_real_marker_missing_or_incomplete(self):
        self.marker['export_complete']=False
        self.write('complete.json',self.marker)
        self.assertEqual(self.request()[0],400)
        (self.directory/'complete.json').unlink()
        self.assertEqual(self.request()[0],404)

    def test_capture_mismatch_in_summary_or_analysis(self):
        self.summary['capture_id']='c'*32
        self.write('summary.json',self.summary)
        self.assertEqual(self.request()[0],400)
        self.summary['capture_id']=ID
        self.write('summary.json',self.summary)
        self.analysis['capture_id']='c'*32
        self.write('analysis.json',self.analysis)
        self.assertEqual(self.request()[0],400)

    def test_real_symlink_input_rejected(self):
        path=self.directory/'analysis.json'
        target=self.root/'outside.json'
        path.rename(target)
        path.symlink_to(target)
        self.assertEqual(self.request()[0],503)

    def test_invalid_json_and_no_auth(self):
        (self.directory/'summary.json').write_text('{broken}')
        self.assertEqual(self.request()[0],400)
        self.assertIn(self.request(auth=False)[0],(401,403))

if __name__=='__main__':unittest.main()
