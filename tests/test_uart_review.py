import copy
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.uart_review import render, export_html

ID = 'a'*32
class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))

class UARTReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.summary=dict(schema_version=1,capture_id=ID,causal=False,completeness='unknown',indexed_bytes=10,unindexed_tail_bytes=2,
            attempts=[dict(attempt_id='attempt-000000',window_available=True,result_available=True,candidate_reports=['report-000000'])],
            diagnostics=[dict(kind='uart_capture',summary='<img src=x onerror=alert(1)>')])
        self.analysis=dict(capture_id=ID,causal=False,reports=[dict(report_id='report-000000',causal=False,association='ambiguous',
            offset_start=0,offset_end=10,connection_epoch=1,termination='gap',truncated=True,associations=[],
            context='before\n<script>alert(1)</script>',text='BUG: synthetic\n frame\n</pre><script>bad</script>')])
        self.marker=dict(schema_version=1,capture_id=ID,export_complete=True)
        for name,data in [('summary.json',self.summary),('analysis.json',self.analysis),('complete.json',self.marker)]:
            (self.root/name).write_text(json.dumps(data))

    def test_html_escapes_all_untrusted_text(self):
        page=render(self.summary,self.analysis,self.marker).decode()
        parser=Tags();parser.feed(page)
        self.assertFalse(any(tag in ('script','img','iframe','object','form') for tag,attrs in parser.tags))
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('BUG: synthetic\n frame',page)
        self.assertIn('Неоднозначная связь',page)
        self.assertTrue(any(tag=='meta' and 'default-src' in attrs.get('content','') for tag,attrs in parser.tags))

    def test_identity_completion_and_semantics_rejected(self):
        for target,key,value in [('marker','export_complete',False),('marker','capture_id','b'*32),('summary','causal',True),('analysis','causal',True)]:
            summary,analysis,marker=copy.deepcopy(self.summary),copy.deepcopy(self.analysis),copy.deepcopy(self.marker)
            {'summary':summary,'analysis':analysis,'marker':marker}[target][key]=value
            with self.assertRaises(ValueError):render(summary,analysis,marker)

    def test_no_candidates_not_claimed_success(self):
        self.analysis['reports']=[]
        self.summary['diagnostics']=[]
        page=render(self.summary,self.analysis,self.marker).decode()
        self.assertIn('не доказывает отсутствие ошибок',page)
        self.assertIn('не гарантия исправности',page)

    def test_exclusive_output_and_originals_unchanged(self):
        original=(self.root/'analysis.json').read_bytes()
        output=export_html(self.root)
        self.assertEqual(output.name,'review.html')
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        before=output.read_bytes()
        with self.assertRaises(FileExistsError):export_html(self.root)
        self.assertEqual(output.read_bytes(),before)
        self.assertEqual((self.root/'analysis.json').read_bytes(),original)
        self.assertEqual(list(self.root.glob('.review-*.tmp')),[])

    def test_symlink_input_rejected(self):
        data=self.root/'summary.json'
        target=self.root/'other.json'
        data.rename(target);data.symlink_to(target)
        with self.assertRaises(OSError):export_html(self.root)
        self.assertFalse((self.root/'review.html').exists())

    def test_size_limit_rejected(self):
        with patch('usb_device_lab.uart_review.MAX_HTML',20):
            with self.assertRaises(ValueError):export_html(self.root)
        self.assertFalse((self.root/'review.html').exists())

    def test_publish_failure_cleans_temporary_file(self):
        with patch('usb_device_lab.uart_review.os.link',side_effect=OSError('link failed')):
            with self.assertRaises(OSError):export_html(self.root)
        self.assertEqual(list(self.root.glob('.review-*.tmp')),[])
        self.assertFalse((self.root/'review.html').exists())

if __name__=='__main__':unittest.main()
