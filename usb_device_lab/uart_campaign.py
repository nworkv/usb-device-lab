"""Agentless Pi campaign. Explicit seeds; no SSH, KCOV or verdict inference."""
import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from .uart_receiver import UARTReceiver
from .uart_reports import AttemptWindow


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def terminate(process, term_timeout=8, kill_timeout=8):
    if process.poll() is not None:
        process.wait(timeout=0)
        return
    for sig, timeout in ((signal.SIGTERM, term_timeout), (signal.SIGKILL, kill_timeout)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
    raise RuntimeError('Executor not reaped after SIGKILL; inspect the stand before retrying')


def run_campaign(config, seeds, *, stop=None, receiver_factory=UARTReceiver,
                 command_factory=None, lock_root=Path('/run/lock/usb-device-lab')):
    from .model import DeviceConfig
    from .mutator import Mutator
    if config.telemetry_mode != 'uart' or config.feedback != 'none' or config.strategy != 'round-robin':
        raise ValueError('Only UART / feedback=none / round-robin is supported')
    if type(config.iterations) is not int or not 1 <= config.iterations <= 10000:
        raise ValueError('This campaign supports 1..10000 attempts')
    for value in (config.seconds, config.pre_run_quiet_seconds, config.post_run_capture_seconds):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 300:
            raise ValueError('Invalid campaign timing')
    if config.seconds == 0 or type(seeds) not in (list, tuple) or not 1 <= len(seeds) <= 128:
        raise ValueError('Supply positive duration and 1..128 explicit seeds')
    parents = []
    for path in seeds:
        data = DeviceConfig.load(path).data
        data['udc_device'], data['udc_driver'] = config.udc, config.gadget_udc_driver
        parents.append(DeviceConfig(data))
    stop = stop if stop is not None else threading.Event()
    lock_root = Path(lock_root)
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = lock_root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe UDC lock directory')
    lock_path = lock_root / (hashlib.sha256(config.udc.encode()).hexdigest() + '.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    receiver, thread, errors, outcomes = None, None, [], []
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError('Unsafe UDC lock file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_size:
            raise RuntimeError('UDC cleanup marker exists; inspect executor/stand before clearing the lock file')
        ident = uuid.uuid4().hex
        root, capture = config.results_dir / ident, config.logs_dir / 'uart' / ident
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            boot = None
        save(root / 'campaign.json', dict(schema_version=1, mode='uart', feedback='none',
            capture_id=ident, capture_directory=str(capture), boot_id=boot, seed=config.seed,
            explicit_seeds=[str(Path(p).resolve()) for p in seeds], iterations=config.iterations,
            seconds=config.seconds, udc=config.udc, udc_driver=config.gadget_udc_driver))
        receiver = receiver_factory(config.uart, capture)
        def receive():
            try:
                receiver.run()
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=receive, name='uart-capture', daemon=True)
        thread.start()
        def healthy():
            if errors or not thread.is_alive() or receiver.status()['state'] in ('failed', 'stopped'):
                raise RuntimeError('UART receiver failed: ' + '; '.join(errors))
        deadline = time.monotonic() + 10
        while receiver.status()['state'] != 'connected':
            if stop.wait(0.02):
                break
            healthy()
            if time.monotonic() >= deadline:
                raise RuntimeError('UART did not open within 10 seconds; executor was not started')
        def pause(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                healthy()
                if stop.wait(min(0.02, max(0, deadline-time.monotonic()))):
                    return False
            return not stop.is_set()
        mutator = Mutator(config.seed)
        for iteration in range(config.iterations):
            if stop.is_set():
                break
            healthy()
            if receiver.status()['state'] != 'connected':
                raise RuntimeError('UART disconnected between attempts; campaign halted')
            parent = parents[iteration % len(parents)]
            child, trace = (parent, []) if iteration < len(parents) else mutator.mutate(parent)
            directory = root / ('attempt-%06d' % iteration)
            directory.mkdir(mode=0o700)
            save(directory / 'config.json', child.data)
            start = time.monotonic_ns()
            save(directory / 'started.json', dict(attempt_id=directory.name, capture_id=ident,
                start_ns=start, parent_digest=parent.digest, mutation=trace, iteration=iteration))
            process, active_start, active_end, fatal = None, start, start, None
            completed = False
            try:
                if pause(config.pre_run_quiet_seconds):
                    healthy()
                    if receiver.status()['state'] != 'connected':
                        raise RuntimeError('UART disconnected before executor launch')
                    argv = command_factory(directory / 'config.json') if command_factory else [sys.executable, '-u', '-m', 'usb_device_lab.device', str(directory / 'config.json')]
                    with (directory / 'executor.log').open('xb') as log:
                        active_start = time.monotonic_ns()
                        try:
                            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
                            deadline = time.monotonic() + config.seconds
                            while process.poll() is None and time.monotonic() < deadline:
                                healthy()
                                if stop.wait(0.02):
                                    break
                            completed = process.poll() is None and not stop.is_set() and time.monotonic() >= deadline
                        finally:
                            active_end = time.monotonic_ns()
                            if process is not None:
                                terminate(process)
                            log.flush()
                            os.fsync(log.fileno())
                    if process.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL) or not completed and not stop.is_set():
                        raise RuntimeError('Executor exited before window completion: %s' % process.returncode)
                    pause(config.post_run_capture_seconds)
            except Exception as error:
                fatal = str(error)
            finally:
                if process is not None and process.poll() is None:
                    marker = memoryview(json.dumps(dict(unreaped_pid=process.pid, campaign_id=ident)).encode())
                    while marker:
                        written = os.write(fd, marker)
                        if written <= 0:
                            raise OSError('Cannot persist UDC cleanup marker')
                        marker = marker[written:]
                    os.fsync(fd)
                active_end = max(active_end, active_start)
                end = max(time.monotonic_ns(), active_end + 1)
                window = AttemptWindow(directory.name, ident, start, end, active_start, active_end)
                save(directory / 'window.json', asdict(window))
                result = dict(iteration=iteration, window_completed=completed,
                    interrupted=stop.is_set(), infrastructure_error=fatal,
                    executor_returncode=process.returncode if process is not None else None,
                    uart_status=receiver.status(), completeness='unknown', coverage_valid=False,
                    pcs=[], causal=False, capture_id=ident)
                save(directory / 'result.json', result)
                outcomes.append(result)
            if fatal:
                raise RuntimeError(fatal)
        return dict(campaign_id=ident, directory=str(root), outcomes=outcomes)
    finally:
        try:
            if receiver is not None:
                receiver.stop()
            if thread is not None:
                thread.join(timeout=config.uart.read_timeout + 2)
                if thread.is_alive():
                    raise RuntimeError('UART thread did not stop; campaign cannot be safely reused')
            if errors:
                raise RuntimeError('UART receiver failed: ' + '; '.join(errors))
        finally:
            os.close(fd)


def main(argv=None):
    from .uart_config import load
    p = argparse.ArgumentParser(description='UART campaign on Pi, no host agent; explicit preselected seeds')
    p.add_argument('--config', required=True)
    p.add_argument('--seeds', nargs='+', required=True, help='Explicit seed files, overriding manifest selection')
    a = p.parse_args(argv)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = run_campaign(load(a.config), a.seeds, stop=stop)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
