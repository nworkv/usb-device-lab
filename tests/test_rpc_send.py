"""Exercise RPC sends with short writes and explicit write failures."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from usb_device_lab.rpc import Lines

class RPCSendTests(unittest.TestCase):
    def make(self, write):
        rpc = Lines.__new__(Lines)
        rpc.stop_event = threading.Event()
        rpc.process = SimpleNamespace(stdin=Mock())
        rpc.process.stdin.write.side_effect = write
        return rpc

    def test_short_writes_preserve_entire_json_line(self):
        received = bytearray()
        def write(data):
            count = min(3, len(data))
            received.extend(data[:count])
            return count
        rpc = self.make(write)
        rpc.send({'command': 'start', 'text': 'тест'})
        self.assertEqual(json.loads(received), {'command': 'start', 'text': 'тест'})
        self.assertTrue(received.endswith(b'\n'))
        self.assertEqual(received.count(b'\n'), 1)
        rpc.process.stdin.flush.assert_called_once_with()

    def test_interrupted_write_retries_same_bytes(self):
        received = bytearray()
        attempts = []
        def write(data):
            attempts.append(bytes(data))
            if len(attempts) == 1:
                raise InterruptedError()
            received.extend(data)
            return len(data)
        rpc = self.make(write)
        rpc.send('stop')
        self.assertEqual(attempts, [b'stop\n', b'stop\n'])
        self.assertEqual(received, b'stop\n')

    def test_no_progress_fails_without_flush(self):
        for count, error in ((0, BrokenPipeError), (None, BlockingIOError)):
            with self.subTest(count=count):
                rpc = self.make(lambda data: count)
                with self.assertRaises(error):
                    rpc.send('start')
                rpc.process.stdin.write.assert_called_once()
                rpc.process.stdin.flush.assert_not_called()

    def test_invalid_write_count_is_rejected(self):
        for count in (-1, 1000, 'bad'):
            with self.subTest(count=count):
                rpc = self.make(lambda data: count)
                with self.assertRaisesRegex(OSError, 'invalid RPC write count'):
                    rpc.send('stop')
                rpc.process.stdin.flush.assert_not_called()

    def test_broken_pipe_is_not_hidden(self):
        def write(data):
            raise BrokenPipeError('closed pipe')
        rpc = self.make(write)
        with self.assertRaisesRegex(BrokenPipeError, 'closed pipe'):
            rpc.send('stop')
        rpc.process.stdin.flush.assert_not_called()

    def test_closed_reader_does_not_write(self):
        rpc = self.make(lambda data: len(data))
        rpc.stop_event.set()
        with self.assertRaises(EOFError):
            rpc.send('start')
        rpc.process.stdin.write.assert_not_called()

    def test_close_during_short_write_stops_next_write(self):
        def write(data):
            rpc.stop_event.set()
            return 1
        rpc = self.make(write)
        with self.assertRaises(EOFError):
            rpc.send('start')
        rpc.process.stdin.write.assert_called_once()
        rpc.process.stdin.flush.assert_not_called()

    def test_flush_error_is_not_hidden(self):
        rpc = self.make(lambda data: len(data))
        rpc.process.stdin.flush.side_effect = OSError('flush failed')
        with self.assertRaisesRegex(OSError, 'flush failed'):
            rpc.send('stop')

if __name__ == '__main__':
    unittest.main()
