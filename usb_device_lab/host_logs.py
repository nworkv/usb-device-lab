"""Bounded per-run /dev/kmsg collection with explicit loss diagnostics."""
import errno
import os
import select
import threading
import time


def parse_kmsg_record(raw):
    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
    header, separator, message = text.partition(';')
    fields = header.split(',')
    if not separator or len(fields) < 4:
        return None
    try:
        return {'priority': int(fields[0]), 'sequence': int(fields[1]),
                'timestamp_usec': int(fields[2]), 'flags': fields[3],
                'message': message}
    except ValueError:
        return None


class KernelLogs:
    def __init__(self, limit=2*1024*1024):
        if type(limit) is not int or limit <= 0:
            raise ValueError('kernel log limit must be a positive integer')
        self.fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            os.lseek(self.fd, 0, os.SEEK_END)
        except Exception:
            os.close(self.fd)
            raise
        self.limit = limit
        self.records = []
        self.size = 0
        self.lost = 0
        self.first_sequence = None
        self.last_sequence = None
        self.sequence_gaps = 0
        self.read_error = None
        self.drain_incomplete = False
        self.done = threading.Event()
        self._closed_result = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        try:
            self.thread.start()
        except Exception:
            os.close(self.fd)
            raise

    def _append(self, raw):
        record = parse_kmsg_record(raw)
        if record is not None:
            sequence = record['sequence']
            if self.first_sequence is None:
                self.first_sequence = sequence
            if self.last_sequence is not None and sequence > self.last_sequence + 1:
                self.sequence_gaps += sequence - self.last_sequence - 1
            self.last_sequence = sequence
        if self.size + len(raw) > self.limit:
            self.lost += 1
        else:
            self.records.append(raw.decode('utf-8', errors='replace'))
            self.size += len(raw)

    def _read_once(self):
        try:
            raw = os.read(self.fd, 8192)
            if not raw:
                self.read_error = 'unexpected end of /dev/kmsg'
                self.lost += 1
                return False
            self._append(raw)
            return True
        except BlockingIOError:
            return False
        except OSError as error:
            self.lost += 1
            if error.errno == errno.EPIPE:
                return True
            self.read_error = str(error)
            return False

    def _read(self):
        while not self.done.is_set() and self.read_error is None:
            try:
                if select.select([self.fd], [], [], 0.1)[0]:
                    self._read_once()
            except OSError as error:
                self.read_error = str(error)
                self.lost += 1
                break

    def close(self):
        if self._closed_result is not None:
            return dict(self._closed_result)
        self.done.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError('kernel log reader failed to stop')
        try:
            deadline = time.monotonic() + 0.2
            while self.read_error is None:
                if not select.select([self.fd], [], [], 0)[0]:
                    break
                if time.monotonic() >= deadline:
                    self.drain_incomplete = True
                    self.lost += 1
                    break
                if not self._read_once():
                    break
        except OSError as error:
            self.read_error = str(error)
            self.lost += 1
        finally:
            os.close(self.fd)
        self._closed_result = {
            'kernel_log': ''.join(self.records),
            'log_dropped': self.lost + self.sequence_gaps,
            'log_bytes': self.size,
            'log_first_sequence': self.first_sequence,
            'log_last_sequence': self.last_sequence,
            'log_sequence_gaps': self.sequence_gaps,
            'log_read_error': self.read_error,
            'log_drain_incomplete': self.drain_incomplete,
        }
        return dict(self._closed_result)
