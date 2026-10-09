"""Linux UART receiver; no host agent, subprocess, UART writes or log decoding."""
import argparse
import errno
import fcntl
import json
import math
import os
import select
import signal
import stat
import termios
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from .uart_config import UARTSettings

class PortBusyError(OSError):
    pass

def check_settings(s):
    if type(s) is not UARTSettings or type(s.baudrate) is not int or not hasattr(termios, 'B' + str(s.baudrate)):
        raise ValueError('Unsupported UART baudrate or settings type')
    if type(s.data_bits) is not int or s.data_bits not in (5, 6, 7, 8) or type(s.stop_bits) is not int or s.stop_bits not in (1, 2) or s.parity not in ('none', 'even', 'odd'):
        raise ValueError('Invalid UART framing')
    for value in (s.read_timeout, s.reconnect_delay):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
            raise ValueError('Invalid UART timeout')
    if type(s.read_chunk_bytes) is not int or not 64 <= s.read_chunk_bytes <= 65536:
        raise ValueError('Invalid UART read size')

class SerialPort:
    def __init__(self, settings):
        check_settings(settings)
        self.settings, self.fd, self.original, self.exclusive = settings, None, None, False
        try:
            fd = os.open(settings.port, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as error:
            if error.errno == errno.EBUSY:
                raise PortBusyError(error.errno, 'UART is busy') from error
            raise
        self.fd = fd
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode) or not os.isatty(fd):
                raise ValueError('UART path is not a tty device')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.ioctl(fd, termios.TIOCEXCL)
                self.exclusive = True
            except OSError as error:
                raise PortBusyError(error.errno, 'UART is busy') from error
            self.original = termios.tcgetattr(fd)
            attrs = self.original[:]
            attrs[6] = self.original[6][:]
            attrs[0], attrs[1], attrs[3] = (termios.INPCK if settings.parity != 'none' else 0), 0, 0
            attrs[2] &= ~(termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB | getattr(termios, 'CRTSCTS', 0) | termios.HUPCL)
            attrs[2] |= termios.CLOCAL | termios.CREAD | getattr(termios, 'CS' + str(settings.data_bits))
            if settings.parity != 'none':
                attrs[2] |= termios.PARENB
            if settings.parity == 'odd':
                attrs[2] |= termios.PARODD
            if settings.stop_bits == 2:
                attrs[2] |= termios.CSTOPB
            attrs[4] = attrs[5] = getattr(termios, 'B' + str(settings.baudrate))
            attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 1, 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception as error:
            self.close()
            if isinstance(error, termios.error):
                raise OSError(error.args[0] if error.args else errno.EIO, str(error)) from error
            raise

    def read(self):
        if not select.select([self.fd], [], [], self.settings.read_timeout)[0]:
            return None
        try:
            data = os.read(self.fd, self.settings.read_chunk_bytes)
        except BlockingIOError:
            return None
        if not data:
            raise OSError(errno.EIO, 'UART disconnected or EOF')
        return data

    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            if self.original is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, self.original)
                except (OSError, termios.error):
                    pass
            if self.exclusive:
                try:
                    fcntl.ioctl(fd, termios.TIOCNXCL)
                except OSError:
                    pass
        finally:
            os.close(fd)

def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('Journal write failed')
        view = view[count:]

class CaptureJournal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.fds, self.offset, self.sequence, self.last_sync = {}, 0, 0, time.monotonic()
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in ('uart.raw', 'chunks.jsonl', 'events.jsonl'):
                self.fds[name] = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except Exception:
            for fd in self.fds.values():
                os.close(fd)
            self.fds = {}
            raise
        finally:
            os.close(directory_fd)

    def json_line(self, name, value):
        write_all(self.fds[name], (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))

    def event(self, kind, epoch, **details):
        self.json_line('events.jsonl', dict(kind=kind, connection_epoch=epoch, offset=self.offset,
            wall_ns=time.time_ns(), monotonic_ns=time.monotonic_ns(), **details))

    def append(self, data, epoch):
        if type(data) is not bytes or not data or len(data) > 65536:
            raise ValueError('Invalid UART chunk')
        wall, mono, start = time.time_ns(), time.monotonic_ns(), self.offset
        write_all(self.fds['uart.raw'], data)
        self.sequence += 1
        end = start + len(data)
        self.json_line('chunks.jsonl', dict(sequence=self.sequence, connection_epoch=epoch,
            offset_start=start, offset_end=end, byte_count=len(data), wall_ns=wall, monotonic_ns=mono))
        self.offset = end
        self.sync_due()

    def sync_due(self, force=False):
        if force or time.monotonic() - self.last_sync >= 1:
            for fd in self.fds.values():
                os.fsync(fd)
            self.last_sync = time.monotonic()

    def close(self):
        try:
            self.sync_due(force=True)
        finally:
            for fd in self.fds.values():
                os.close(fd)
            self.fds = {}

class UARTReceiver:
    def __init__(self, settings, directory, port_factory=SerialPort):
        check_settings(settings)
        self.settings, self.directory, self.port_factory = settings, Path(directory), port_factory
        self.stop_event, self._lock = threading.Event(), threading.RLock()
        self._status = dict(state='new', connection_epoch=0, bytes_received=0, reconnects=0,
                            last_read_monotonic_ns=None, completeness='unknown', error=None)

    def status(self):
        with self._lock:
            return dict(self._status)

    def update(self, **values):
        with self._lock:
            self._status.update(values)

    def stop(self):
        self.stop_event.set()

    def run(self):
        with self._lock:
            if self._status['state'] != 'new':
                raise RuntimeError('Receiver cannot be reused')
            self._status['state'] = 'starting'
        journal, port, epoch = None, None, 0
        try:
            journal = CaptureJournal(self.directory)
            journal.event('started', epoch, settings=asdict(self.settings), completeness='unknown')
            while not self.stop_event.is_set():
                if port is None:
                    try:
                        port = self.port_factory(self.settings)
                    except PortBusyError:
                        raise
                    except OSError as error:
                        if error.errno in (errno.EINVAL, errno.ENOTTY):
                            raise
                        self.update(state='reconnecting', error=str(error))
                        journal.event('open_failed', epoch, errno=error.errno, loss='unknown')
                        journal.sync_due(force=True)
                        if self.stop_event.wait(self.settings.reconnect_delay):
                            break
                        continue
                    epoch += 1
                    self.update(state='connected', connection_epoch=epoch, reconnects=epoch-1, error=None)
                    journal.event('connected', epoch)
                try:
                    data = port.read()
                except OSError as error:
                    journal.event('disconnected', epoch, errno=error.errno, loss='unknown')
                    journal.sync_due(force=True)
                    port.close()
                    port = None
                    self.update(state='reconnecting', error=str(error))
                    if self.stop_event.wait(self.settings.reconnect_delay):
                        break
                    continue
                if data is not None:
                    journal.append(data, epoch)
                    self.update(bytes_received=journal.offset, last_read_monotonic_ns=time.monotonic_ns())
                else:
                    journal.sync_due()
            journal.event('stopped', epoch, completeness='unknown')
            self.update(state='stopped')
        except Exception as error:
            self.update(state='failed', error=str(error))
            if journal is not None:
                try:
                    journal.event('failed', epoch, error_type=type(error).__name__)
                except Exception:
                    pass
            raise
        finally:
            try:
                if port is not None:
                    port.close()
            finally:
                if journal is not None:
                    try:
                        journal.close()
                    except Exception as error:
                        self.update(state='failed', error=str(error))
                        raise
        return self.status()

def main(argv=None):
    from .uart_config import load
    parser = argparse.ArgumentParser(description='Continuous UART capture on Pi, without a host agent')
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', help='New capture directory; existing paths are never overwritten')
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        check_settings(config.uart)
    except (OSError, ValueError) as error:
        parser.exit(2, 'UART configuration error: ' + str(error) + '\n')
    directory = Path(args.output) if args.output else config.logs_dir / 'uart' / uuid.uuid4().hex
    receiver = UARTReceiver(config.uart, directory)
    previous = {sig: signal.signal(sig, lambda signum, frame: receiver.stop()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        print('UART capture directory: ' + str(directory), flush=True)
        try:
            receiver.run()
        except (OSError, ValueError) as error:
            parser.exit(1, 'UART capture failed: ' + str(error) + '\n')
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
