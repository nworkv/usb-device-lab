import unittest
from usb_device_lab.uart_reports import AttemptWindow, analyze

class UARTReportTests(unittest.TestCase):
    def fixture(self, parts):
        raw, chunks = b'', []
        for seq, (data, stamp, epoch) in enumerate(parts, 1):
            start = len(raw)
            raw += data
            chunks.append(dict(sequence=seq, offset_start=start, offset_end=len(raw), byte_count=len(data), monotonic_ns=stamp, connection_epoch=epoch))
        return raw, chunks

    def window(self, name='a', start=10, end=50, active_start=20, active_end=40, capture='capture'):
        return AttemptWindow(name, capture, start, end, active_start, active_end)

    def run_analysis(self, parts, windows=None, **kwargs):
        raw, chunks = self.fixture(parts)
        return analyze(raw, chunks, windows if windows is not None else [self.window()], capture_id='capture', **kwargs)

    def test_split_header_utf8_and_context(self):
        result = self.run_analysis([(b'context\n[ 1.0] BU', 21, 1), (b'G: KASAN: \xd0', 22, 1), (b'\x90\nCall Trace:\n frame\n---[ end trace abc ]---\n', 23, 1)])
        report = result['reports'][0]
        self.assertIn('BUG: KASAN: А', report['text'])
        self.assertEqual(report['context'], 'context\n')
        self.assertEqual(report['termination'], 'end_trace_marker')
        self.assertFalse(report['causal'])

    def test_half_open_windows(self):
        r = self.run_analysis([(b'one\n', 10, 1), (b'two\n', 50, 1)])
        self.assertEqual(r['fragments'][0]['ranges'][0]['offset_end'], 4)

    def test_ambiguous_overlap(self):
        r = self.run_analysis([(b'BUG: error\nstack\n', 25, 1)], [self.window(), self.window('b')])
        self.assertEqual(r['reports'][0]['association'], 'ambiguous')
        self.assertEqual(r['fragments'][0]['overlapping_attempts'], ['b'])

    def test_post_capture_not_causation(self):
        r = self.run_analysis([(b'WARNING: late\n', 45, 1)])
        self.assertEqual(r['reports'][0]['associations'][0]['receive_phases'], ['post'])
        self.assertEqual(r['reports'][0]['association'], 'candidate')

    def test_unassigned_report(self):
        r = self.run_analysis([(b'Oops: outside\n', 70, 1)])
        self.assertEqual(r['reports'][0]['association'], 'unassigned')

    def test_epoch_break_never_merges_reports(self):
        r = self.run_analysis([(b'BUG: before\n', 21, 1), (b' tail\nWARNING: after\n', 22, 2)])
        self.assertEqual(len(r['reports']), 2)
        self.assertEqual(r['reports'][0]['termination'], 'gap')
        self.assertNotIn('tail', r['reports'][0]['text'])
        self.assertEqual(len(r['fragments'][0]['ranges']), 2)

    def test_partial_header_across_gap_not_detected(self):
        r = self.run_analysis([(b'BU', 21, 1), (b'G: error\n', 22, 2)])
        self.assertEqual(r['reports'], [])

    def test_event_gap_even_same_epoch(self):
        r = self.run_analysis([(b'BUG: first\n', 21, 1), (b'next\n', 22, 1)], events=[dict(kind='disconnected', offset=11)])
        self.assertEqual(r['reports'][0]['termination'], 'gap')
        self.assertEqual(len(r['fragments'][0]['ranges']), 2)

    def test_adjacent_reports_and_limit(self):
        r = self.run_analysis([(b'BUG: first\nWARNING: second\nframe\n', 21, 1)], report_lines=2)
        self.assertEqual([x['termination'] for x in r['reports']], ['next_header', 'limit'])
        self.assertTrue(r['reports'][1]['truncated'])

    def test_unindexed_tail_not_parsed(self):
        raw, chunks = self.fixture([(b'ok\n', 21, 1)])
        r = analyze(raw + b'BUG: orphan\n', chunks, [self.window()], capture_id='capture')
        self.assertEqual(r['unindexed_tail_bytes'], 12)
        self.assertEqual(r['reports'], [])

    def test_invalid_indices(self):
        for key, value in [('sequence', True), ('offset_start', 1), ('monotonic_ns', -1), ('connection_epoch', 0), ('byte_count', 999)]:
            raw, chunks = self.fixture([(b'ok\n', 21, 1)])
            chunks[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze(raw, chunks, [], capture_id='capture')

    def test_window_identity_and_order(self):
        for windows in [[self.window(capture='other')], [self.window(), self.window()], [self.window(start=30)]]:
            with self.assertRaises(ValueError):
                self.run_analysis([(b'ok\n', 21, 1)], windows)

    def test_silence_not_hang(self):
        r = self.run_analysis([])
        self.assertEqual(r['reports'], [])
        self.assertEqual(r['fragments'][0]['ranges'], [])
        self.assertEqual(r['completeness'], 'unknown')

    def test_binary_text_and_capture_end(self):
        r = self.run_analysis([(b'UBSAN: bad \xff\x00\n', 21, 1)])
        self.assertIn('\\xff', r['reports'][0]['text'])
        self.assertEqual(r['reports'][0]['termination'], 'capture_end')

if __name__ == '__main__':
    unittest.main()
