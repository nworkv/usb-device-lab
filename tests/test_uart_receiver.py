import errno
import json
import os
import pty
import select
import tempfile
import termios
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.uart_config import UARTSettings
from usb_device_lab.uart_receiver import UARTReceiver, SerialPort, CaptureJournal, PortBusyError

class UARTReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root, self.fds, self.readers, self.errors = Path(self.tmp.name), [], [], []

    def tearDown(self):
        for r, t in self.readers:
            r.stop()
            t.join(2)
            self.assertFalse(t.is_alive())
        for fd in self.fds:
            os.close(fd)
        self.tmp.cleanup()

    def terminal(self):
        m, s = pty.openpty()
        self.fds.extend((m, s))
        return m, s, os.ttyname(s)

    def settings(self, path):
        return UARTSettings(path, read_timeout=0.02, reconnect_delay=0.02, read_chunk_bytes=128)

    def wait(self, condition):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.005)
        self.fail('Capture timed out')

    def start(self, path, factory=SerialPort):
        r = UARTReceiver(self.settings(path), self.root / ('capture-' + str(len(self.readers))), factory)
        def run():
            try:
                r.run()
            except Exception as error:
                self.errors.append(error)
        t = threading.Thread(target=run)
        self.readers.append((r, t))
        t.start()
        return r, t

    def stop(self, r, t):
        r.stop()
        t.join(2)
        self.assertFalse(t.is_alive())

    def records(self, r, name):
        return [json.loads(line) for line in (r.directory / name).read_text().splitlines()]

    def test_binary_fragments_offsets_and_times(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        os.write(m, bytes([208]))
        self.wait(lambda: r.status()['bytes_received'] == 1)
        os.write(m, bytes([144, 0, 255, 13, 10]))
        self.wait(lambda: r.status()['bytes_received'] == 6)
        self.stop(r, t)
        self.assertEqual((r.directory / 'uart.raw').read_bytes(), bytes([208, 144, 0, 255, 13, 10]))
        end = 0
        for seq, chunk in enumerate(self.records(r, 'chunks.jsonl'), 1):
            self.assertEqual(chunk['sequence'], seq)
            self.assertEqual(chunk['offset_start'], end)
            self.assertEqual(chunk['byte_count'], chunk['offset_end'] - end)
            self.assertIsInstance(chunk['wall_ns'], int)
            self.assertIsInstance(chunk['monotonic_ns'], int)
            end = chunk['offset_end']
        self.assertEqual(end, 6)
        self.assertEqual(self.errors, [])

    def test_idle_is_not_disconnect(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        time.sleep(0.08)
        self.stop(r, t)
        self.assertNotIn('disconnected', [e['kind'] for e in self.records(r, 'events.jsonl')])
        self.assertEqual(r.status()['completeness'], 'unknown')
        self.assertEqual(r.status()['bytes_received'], 0)

    def test_no_echo_or_transmit(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        os.write(m, b'input\n')
        self.wait(lambda: r.status()['bytes_received'] == 6)
        self.assertEqual(select.select([m], [], [], 0.05)[0], [])
        self.stop(r, t)

    def test_terminal_settings_restored(self):
        m, s, path = self.terminal()
        old = termios.tcgetattr(s)
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        self.stop(r, t)
        self.assertEqual(termios.tcgetattr(s), old)

    def test_chunk_limit(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        data = bytes(range(256)) * 8
        os.write(m, data)
        self.wait(lambda: r.status()['bytes_received'] == len(data))
        self.stop(r, t)
        self.assertEqual((r.directory / 'uart.raw').read_bytes(), data)
        self.assertTrue(all(c['byte_count'] <= 128 for c in self.records(r, 'chunks.jsonl')))

    def test_real_disconnect_and_reconnect(self):
        m1, s1, p1 = self.terminal()
        m2, s2, p2 = self.terminal()
        gate, calls = threading.Event(), []
        def factory(settings):
            if not calls:
                calls.append(p1)
                return SerialPort(settings)
            if not gate.is_set():
                raise FileNotFoundError(errno.ENOENT, 'Disconnected fixture')
            return SerialPort(replace(settings, port=p2))
        r, t = self.start(p1, factory)
        self.wait(lambda: r.status()['state'] == 'connected')
        os.write(m1, b'before')
        self.wait(lambda: r.status()['bytes_received'] == 6)
        os.close(m1)
        self.fds.remove(m1)
        self.wait(lambda: r.status()['state'] == 'reconnecting')
        gate.set()
        self.wait(lambda: r.status()['connection_epoch'] == 2)
        os.write(m2, b'after')
        self.wait(lambda: r.status()['bytes_received'] == 11)
        self.stop(r, t)
        self.assertEqual((r.directory / 'uart.raw').read_bytes(), b'beforeafter')
        self.assertIn('disconnected', [e['kind'] for e in self.records(r, 'events.jsonl')])
        self.assertEqual(r.status()['reconnects'], 1)
        self.assertEqual(self.errors, [])

    def test_stop_when_missing(self):
        r, t = self.start('/dev/serial/by-id/nonexistent-test-adapter')
        self.wait(lambda: r.status()['state'] == 'reconnecting')
        self.stop(r, t)
        self.assertEqual(r.status()['state'], 'stopped')
        self.assertEqual(self.errors, [])

    def test_second_reader_rejected(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        other = UARTReceiver(self.settings(path), self.root / 'second')
        with self.assertRaises(PortBusyError):
            other.run()
        self.assertEqual(other.status()['state'], 'failed')
        self.stop(r, t)

    def test_existing_directory_preserved(self):
        directory = self.root / 'existing'
        directory.mkdir()
        (directory / 'uart.raw').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            CaptureJournal(directory)
        self.assertEqual((directory / 'uart.raw').read_bytes(), b'preserve')

    def test_bad_baud_and_regular_file(self):
        m, s, path = self.terminal()
        with self.assertRaises(ValueError):
            UARTReceiver(replace(self.settings(path), baudrate=123456), self.root / 'bad')
        regular = self.root / 'regular'
        regular.write_bytes(b'not tty')
        with self.assertRaises(ValueError):
            SerialPort(replace(self.settings(path), port=str(regular)))

    def test_storage_failure_is_fatal(self):
        m, s, path = self.terminal()
        with patch('usb_device_lab.uart_receiver.CaptureJournal.append', side_effect=OSError('storage failed')):
            r, t = self.start(path)
            self.wait(lambda: r.status()['state'] == 'connected')
            os.write(m, b'payload')
            t.join(2)
            self.assertFalse(t.is_alive())
        self.assertEqual(r.status()['state'], 'failed')
        self.assertEqual(r.status()['connection_epoch'], 1)
        self.assertTrue(any('storage failed' in str(e) for e in self.errors))

    def test_permissions_and_one_shot(self):
        m, s, path = self.terminal()
        r, t = self.start(path)
        self.wait(lambda: r.status()['state'] == 'connected')
        self.stop(r, t)
        for name in ('uart.raw', 'chunks.jsonl', 'events.jsonl'):
            self.assertEqual((r.directory / name).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(RuntimeError):
            r.run()

if __name__ == '__main__':
    unittest.main()
