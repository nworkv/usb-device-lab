import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.log_tail import MAX_BYTES, list_logs, read_tail

RUN = 'a' * 32


class LogTailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'runs' / RUN
        self.directory.mkdir(parents=True)

    def test_list_available_and_missing_logs(self):
        (self.directory / 'executor.log').write_text('hello')
        rows = list_logs(self.root, RUN)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]['available'])
        self.assertEqual(rows[0]['size'], 5)
        self.assertFalse(rows[1]['available'])

    def test_last_lines_only(self):
        (self.directory / 'executor.log').write_text('one\ntwo\nthree\n')
        result = read_tail(self.root, RUN, 'executor', 2)
        self.assertEqual(result['text'], 'two\nthree\n')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['lines_returned'], 2)

    def test_byte_limit_on_large_single_line(self):
        (self.directory / 'executor.log').write_bytes(b'x' * (MAX_BYTES * 4))
        result = read_tail(self.root, RUN, 'executor')
        self.assertEqual(result['bytes_read'], MAX_BYTES)
        self.assertEqual(len(result['text']), MAX_BYTES)
        self.assertTrue(result['partial_first_line_possible'])

    def test_empty_file(self):
        (self.directory / 'agent.log').touch()
        result = read_tail(self.root, RUN, 'agent')
        self.assertEqual(result['text'], '')
        self.assertEqual(result['lines_returned'], 0)

    def test_invalid_utf8_is_replaced(self):
        (self.directory / 'agent.log').write_bytes(bytes([255]) + b'hello')
        self.assertIn('hello', read_tail(self.root, RUN, 'agent')['text'])

    def test_invalid_run_source_and_limits(self):
        for run, source, lines in (('../outside', 'executor', 1), (RUN, '../agent.log', 1),
                                   (RUN, 'executor', 0), (RUN, 'executor', 1001), (RUN, 'executor', True)):
            with self.subTest(run=run, source=source, lines=lines), self.assertRaises(ValueError):
                read_tail(self.root, run, source, lines)

    def test_file_symlink_is_not_followed(self):
        outside = self.root / 'secret'
        outside.write_text('private')
        (self.directory / 'executor.log').symlink_to(outside)
        with self.assertRaises(OSError):
            read_tail(self.root, RUN, 'executor')
        self.assertFalse(list_logs(self.root, RUN)[0]['available'])

    def test_run_directory_symlink_is_not_followed(self):
        self.directory.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'executor.log').write_text('private')
        self.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            read_tail(self.root, RUN, 'executor')

    def test_runs_directory_symlink_is_not_followed(self):
        self.directory.rmdir()
        (self.root / 'runs').rmdir()
        outside = self.root / 'outside'
        (outside / RUN).mkdir(parents=True)
        (outside / RUN / 'executor.log').write_text('private')
        (self.root / 'runs').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            read_tail(self.root, RUN, 'executor')

    def test_fifo_does_not_block(self):
        os.mkfifo(self.directory / 'executor.log')
        with self.assertRaises(ValueError):
            read_tail(self.root, RUN, 'executor')

    def test_missing_log_and_saved_kernel_label(self):
        with self.assertRaises(FileNotFoundError):
            read_tail(self.root, RUN, 'kernel')
        (self.directory / 'kmsg.delta.log').write_text('kernel event')
        self.assertTrue(read_tail(self.root, RUN, 'kernel')['saved_kernel_log'])

    def test_file_growth_is_reported(self):
        path = self.directory / 'executor.log'
        path.write_bytes(b'one\n')
        original = os.pread
        def growing(descriptor, count, offset):
            data = original(descriptor, count, offset)
            with path.open('ab') as target:
                target.write(b'two\n')
            return data
        with patch('usb_device_lab.log_tail.os.pread', side_effect=growing):
            self.assertTrue(read_tail(self.root, RUN, 'executor')['changed_during_read'])


if __name__ == '__main__':
    unittest.main()
