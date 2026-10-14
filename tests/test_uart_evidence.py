import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.uart_evidence import export_campaign, jsonl, decode

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ident = 'a' * 32
        self.campaign = self.root / self.ident
        self.capture_root = self.root / 'captures'
        self.capture = self.capture_root / self.ident
        self.campaign.mkdir()
        self.capture.mkdir(parents=True)
        self.attempt = self.campaign / 'attempt-000000'
        self.attempt.mkdir()
        self.write(self.campaign / 'campaign.json', dict(schema_version=1, mode='uart', feedback='none', capture_id=self.ident, capture_directory='/untrusted/path'))
        self.write(self.attempt / 'started.json', dict(attempt_id=self.attempt.name, capture_id=self.ident, start_ns=10))
        self.write(self.attempt / 'window.json', dict(attempt_id=self.attempt.name, capture_id=self.ident, start_ns=10, end_ns=50, active_start_ns=20, active_end_ns=40))
        self.write(self.attempt / 'result.json', dict(capture_id=self.ident, window_completed=True, interrupted=False, infrastructure_error=None, executor_returncode=-15))
        self.raw(b'context\nBUG: KASAN: bad\n frame\n', stamp=25)
        (self.capture / 'events.jsonl').write_bytes(b'')

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def raw(self, data, stamp=25):
        (self.capture / 'uart.raw').write_bytes(data)
        c = dict(sequence=1, offset_start=0, offset_end=len(data), byte_count=len(data), monotonic_ns=stamp, connection_epoch=1)
        (self.capture / 'chunks.jsonl').write_text(json.dumps(c) + '\n' if data else '')

    def export(self):
        return export_campaign(self.campaign, self.capture_root)

    def load(self, output, name='summary.json'):
        return json.loads((output / name).read_text())

    def test_export_and_originals_unchanged(self):
        original = (self.attempt / 'result.json').read_bytes()
        output = self.export()
        summary = self.load(output)
        self.assertEqual(summary['kernel_candidate_count'], 1)
        self.assertEqual(summary['attempts'][0]['candidate_reports'], ['report-000000'])
        report = self.load(output, 'report-000000.json')
        self.assertEqual(report['context'], 'context\n')
        self.assertEqual(report['kind'], 'kernel_report_candidate')
        self.assertFalse(report['causal'])
        self.assertEqual(original, (self.attempt / 'result.json').read_bytes())
        self.assertTrue(self.load(output, 'complete.json')['export_complete'])

    def test_repeat_export_new_directory(self):
        first, second = self.export(), self.export()
        self.assertNotEqual(first, second)
        self.assertEqual(self.load(first), self.load(second))

    def test_separate_uart_executor_and_infrastructure(self):
        (self.capture / 'events.jsonl').write_text(json.dumps(dict(kind='disconnected', offset=0)) + '\n')
        self.write(self.attempt / 'result.json', dict(capture_id=self.ident, window_completed=False, interrupted=False, infrastructure_error='executor failed', executor_returncode=2))
        summary = self.load(self.export())
        self.assertEqual({d['kind'] for d in summary['diagnostics']}, {'uart_capture', 'executor_exit', 'campaign_infrastructure'})
        self.assertEqual(summary['kernel_candidate_count'], 1)

    def test_silence_is_not_hang(self):
        self.raw(b'')
        summary = self.load(self.export())
        self.assertEqual(summary['observation'], 'no_indexed_uart_bytes')
        self.assertEqual(summary['kernel_candidate_count'], 0)
        self.assertEqual(summary['completeness'], 'unknown')

    def test_partial_jsonl_and_unindexed_raw(self):
        with (self.capture / 'chunks.jsonl').open('ab') as f:
            f.write(b'{"sequence":2')
        with (self.capture / 'uart.raw').open('ab') as f:
            f.write(b'BUG: orphan\n')
        summary = self.load(self.export())
        self.assertEqual(summary['unindexed_tail_bytes'], 12)
        self.assertIn('metadata_tail', [d['kind'] for d in summary['diagnostics']])
        self.assertEqual(summary['kernel_candidate_count'], 1)

    def test_complete_malformed_record_rejected(self):
        (self.capture / 'events.jsonl').write_bytes(b'{broken}\n')
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(list(self.campaign.glob('evidence-*')), [])

    def test_missing_window_and_result_diagnosed(self):
        (self.attempt / 'window.json').unlink()
        (self.attempt / 'result.json').unlink()
        summary = self.load(self.export())
        self.assertEqual(len(summary['diagnostics']), 2)
        self.assertFalse(summary['attempts'][0]['window_available'])
        self.assertEqual(summary['attempts'][0]['candidate_reports'], [])

    def test_identity_mismatch_rejected(self):
        value = json.loads((self.attempt / 'window.json').read_text())
        value['capture_id'] = 'b' * 32
        self.write(self.attempt / 'window.json', value)
        with self.assertRaises(ValueError):
            self.export()

    def test_capture_file_symlink_rejected(self):
        target = self.root / 'external'
        target.write_bytes(b'private')
        (self.capture / 'uart.raw').unlink()
        (self.capture / 'uart.raw').symlink_to(target)
        with self.assertRaises(OSError):
            self.export()

    def test_fifo_rejected_without_blocking(self):
        (self.capture / 'uart.raw').unlink()
        os.mkfifo(self.capture / 'uart.raw')
        with self.assertRaises(ValueError):
            self.export()

    def test_duplicate_keys_nonfinite_and_tail(self):
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]'):
            with self.assertRaises(ValueError):
                decode(data)
        self.assertEqual(jsonl(b'{"x":1}'), ([], 7))

    def test_raw_limit_rejected(self):
        with patch('usb_device_lab.uart_evidence.MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                self.export()

    def test_export_failure_has_no_complete_marker(self):
        with patch('usb_device_lab.uart_evidence.os.fsync', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.export()
        output = list(self.campaign.glob('evidence-*'))[0]
        self.assertFalse((output / 'complete.json').exists())

    def test_output_permissions(self):
        output = self.export()
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))

if __name__ == '__main__':
    unittest.main()
