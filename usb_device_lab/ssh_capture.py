"""Agentless SSH kernel-log capture with optional remote lifetime and bounded local cleanup."""
from dataclasses import asdict
import os
import select
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from .dual_config import SSHSettings, ssh_settings
from .uart_receiver import CaptureJournal

READY = b'USB_DEVICE_LAB_READY_V1'


def command(settings):
    ssh_settings(asdict(settings))
    prefix = 'sudo -n ' if settings.use_sudo else ''
    script = (
        'dmesg --help | grep -q -- --follow-new || exit 71; '
        + prefix + 'dmesg --raw >/dev/null || exit 72; '
        'reader=; watcher=; '
        'cleanup() { [ -z "$reader" ] || kill "$reader" 2>/dev/null; '
        '[ -z "$watcher" ] || kill "$watcher" 2>/dev/null; '
        '[ -z "$reader" ] || wait "$reader" 2>/dev/null; }; '
        "trap cleanup EXIT; trap 'exit 143' HUP INT TERM; "
        'exec 3<&0; '
        + prefix + 'dmesg --follow-new --raw </dev/null & reader=$!; '
        'sleep 0.1; kill -0 "$reader" 2>/dev/null || exit 73; '
        "printf '%s\n' 'USB_DEVICE_LAB_READY_V1' >&2; "
        '(cat <&3 >/dev/null; kill "$reader" 2>/dev/null) & watcher=$!; '
        'wait "$reader"'
    )
    remote = 'timeout --signal=TERM --kill-after=2 ' + str(settings.lease_seconds) + ' sh -c ' + shlex.quote(script)
    options = {
        'BatchMode': 'yes', 'StrictHostKeyChecking': 'yes', 'IdentitiesOnly': 'yes',
        'PasswordAuthentication': 'no', 'KbdInteractiveAuthentication': 'no',
        'UserKnownHostsFile': settings.known_hosts_file, 'GlobalKnownHostsFile': '/dev/null',
        'ConnectTimeout': str(settings.connect_timeout), 'ConnectionAttempts': '1',
        'ServerAliveInterval': '2', 'ServerAliveCountMax': '2', 'ControlMaster': 'no',
        'ControlPath': 'none', 'ForwardAgent': 'no', 'ClearAllForwardings': 'yes',
        'RequestTTY': 'no',
    }
    argv = ['ssh', '-F', '/dev/null', '-T', '-p', str(settings.port), '-i', settings.identity_file]
    for key, value in options.items():
        argv.extend(['-o', key + '=' + value])
    return argv + ['--', settings.user + '@' + settings.host, remote]


def close_process(process):
    if process.stdin is not None:
        process.stdin.close()
    if process.poll() is None:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
    if process.poll() is None:
        raise RuntimeError('SSH process was not reaped; inspect before retrying')
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


class SSHReceiver:
    def __init__(self, settings, directory, process_factory=None):
        if type(settings) is not SSHSettings:
            raise ValueError('SSHSettings required')
        ssh_settings(asdict(settings))
        self.settings, self.directory = settings, Path(directory)
        self.process_factory = process_factory or subprocess.Popen
        self.stop_event, self.lock = threading.Event(), threading.RLock()
        self._status = dict(state='new', transport='ssh', connection_epoch=0, bytes_received=0,
                            reconnects=0, completeness='unknown', error=None, lifetime_seconds=settings.lease_seconds,
                            returncode=None, failure_kind=None)

    def status(self):
        with self.lock:
            return dict(self._status)

    def update(self, **values):
        with self.lock:
            self._status.update(values)

    def stop(self):
        self.stop_event.set()

    def run(self):
        with self.lock:
            if self._status['state'] != 'new':
                raise RuntimeError('Receiver cannot be reused')
            self._status['state'] = 'starting'
        journal, process, ready = None, None, False
        stderr_buffer, stderr_tail = bytearray(), bytearray()
        try:
            journal = CaptureJournal(self.directory)
            journal.event('started', 0, transport='ssh', host=self.settings.host,
                          source='dmesg --follow-new --raw', completeness='unknown', lifetime_seconds=self.settings.lease_seconds)
            process = self.process_factory(command(self.settings), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
            streams = {process.stdout.fileno(): 'stdout', process.stderr.fileno(): 'stderr'}
            for fd in streams:
                os.set_blocking(fd, False)
            deadline = time.monotonic() + self.settings.connect_timeout + 2
            while not self.stop_event.is_set():
                if not ready and time.monotonic() > deadline:
                    raise RuntimeError('SSH readiness timeout; no USB executor should be started')
                for fd in select.select(list(streams), [], [], self.settings.read_timeout)[0]:
                    try:
                        data = os.read(fd, self.settings.read_chunk_bytes)
                    except BlockingIOError:
                        continue
                    if not data:
                        streams.pop(fd)
                        continue
                    if streams[fd] == 'stdout':
                        journal.append(data, 1)
                        self.update(bytes_received=journal.offset, last_read_monotonic_ns=time.monotonic_ns())
                    else:
                        stderr_tail.extend(data)
                        stderr_tail = stderr_tail[-16384:]
                        stderr_buffer.extend(data)
                        while b'\n' in stderr_buffer:
                            line, _, remaining = stderr_buffer.partition(b'\n')
                            stderr_buffer = bytearray(remaining)
                            if line.rstrip(b'\r') == READY and not ready:
                                ready = True
                                journal.event('connected', 1, transport='ssh', readiness='remote_log_reader_probe')
                                self.update(state='connected', connection_epoch=1)
                        stderr_buffer = stderr_buffer[-65536:]
                journal.sync_due()
                if process.poll() is not None:
                    if self.stop_event.is_set():
                        break
                    rc = process.returncode
                    expired = rc == 124 and self.settings.lease_seconds > 0
                    self.update(returncode=rc, failure_kind='lifetime_expired' if expired else 'remote_stream_exit')
                    reason = ('SSH capture lifetime expired after %s seconds; set ssh.lease_seconds=0 for no lifetime limit' % self.settings.lease_seconds) if expired else 'SSH stream ended unexpectedly'
                    raise RuntimeError(reason + ', returncode=' + str(rc))
                if not streams:
                    raise RuntimeError('SSH streams closed unexpectedly')
            journal.event('stopped', 1 if ready else 0, transport='ssh', completeness='unknown')
            self.update(state='stopped')
        except Exception as error:
            self.update(state='failed', error=str(error))
            if journal is not None:
                journal.event('failed', 1 if ready else 0, transport='ssh', error_type=type(error).__name__, loss='unknown')
            raise
        finally:
            try:
                if process is not None:
                    close_process(process)
            except Exception as error:
                self.update(state='failed', error=str(error))
                raise
            finally:
                if journal is not None:
                    try:
                        if stderr_tail:
                            path = self.directory / 'ssh-stderr.log'
                            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                            with os.fdopen(fd, 'wb') as f:
                                f.write(stderr_tail); f.flush(); os.fsync(f.fileno())
                    except Exception as error:
                        self.update(state='failed', error=str(error))
                        raise
                    finally:
                        try:
                            journal.close()
                        except Exception as error:
                            self.update(state='failed', error=str(error))
                            raise
        return self.status()
