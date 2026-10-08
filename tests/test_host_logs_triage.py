"""Kernel log collector tests using mocked I/O, without root or /dev/kmsg."""
import errno
import threading
import unittest
from unittest.mock import Mock, patch

from usb_device_lab.host_logs import KernelLogs, parse_kmsg_record


def collector(limit=1024):
    logs = KernelLogs.__new__(KernelLogs)
    logs.fd = 123
    logs.limit = limit
    logs.records = []
    logs.size = logs.lost = logs.sequence_gaps = 0
    logs.first_sequence = logs.last_sequence = None
    logs.read_error = None
    logs.drain_incomplete = False
    logs.done = threading.Event()
    logs._closed_result = None
    logs.thread = Mock()
    logs.thread.is_alive.return_value = False
    return logs


class HostLogsTriageTests(unittest.TestCase):
    def test_parse_record_and_reject_malformed_header(self):
        record = parse_kmsg_record(b'3,12,456,-;BUG: test\n')
        self.assertEqual(record['sequence'], 12)
        self.assertEqual(record['timestamp_usec'], 456)
        self.assertEqual(record['message'], 'BUG: test\n')
        self.assertIsNone(parse_kmsg_record(b'not a kmsg record'))
        self.assertIsNone(parse_kmsg_record(b'3,bad,456,-;test'))

    def test_limit_counts_bytes_not_unicode_characters(self):
        logs = collector(limit=1)
        logs._append('я'.encode('utf-8'))
        self.assertEqual(logs.records, [])
        self.assertEqual(logs.lost, 1)
        self.assertEqual(logs.size, 0)

    def test_sequence_gap_is_recorded(self):
        logs = collector()
        logs._append(b'6,10,1,-;first\n')
        logs._append(b'6,13,2,-;last\n')
        self.assertEqual(logs.first_sequence, 10)
        self.assertEqual(logs.last_sequence, 13)
        self.assertEqual(logs.sequence_gaps, 2)

    def test_ring_overrun_does_not_end_reading(self):
        logs = collector()
        with patch('usb_device_lab.host_logs.os.read', side_effect=OSError(errno.EPIPE, 'overrun')):
            self.assertTrue(logs._read_once())
        self.assertEqual(logs.lost, 1)
        self.assertIsNone(logs.read_error)

    def test_fatal_read_error_is_explicit(self):
        logs = collector()
        with patch('usb_device_lab.host_logs.os.read', side_effect=OSError(errno.EBADF, 'bad descriptor')):
            self.assertFalse(logs._read_once())
        self.assertIsNotNone(logs.read_error)
        self.assertEqual(logs.lost, 1)

    def test_close_drains_and_is_idempotent(self):
        logs = collector()
        with patch('usb_device_lab.host_logs.select.select', side_effect=[([123], [], []), ([], [], [])]), \
             patch('usb_device_lab.host_logs.os.read', return_value=b'3,15,5,-;WARNING: late report\n'), \
             patch('usb_device_lab.host_logs.os.close') as close_fd:
            first = logs.close()
            second = logs.close()
        self.assertIn('WARNING: late report', first['kernel_log'])
        self.assertEqual(first, second)
        self.assertEqual(first['log_dropped'], 0)
        close_fd.assert_called_once_with(123)
        logs.thread.join.assert_called_once_with(timeout=2)

    def test_close_does_not_close_fd_while_reader_is_alive(self):
        logs = collector()
        logs.thread.is_alive.return_value = True
        with patch('usb_device_lab.host_logs.os.close') as close_fd:
            with self.assertRaises(RuntimeError):
                logs.close()
        close_fd.assert_not_called()


if __name__ == '__main__':
    unittest.main()
