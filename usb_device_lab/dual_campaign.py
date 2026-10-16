"""Shared UART/SSH executor with cooperative per-attempt skip control."""
from dataclasses import asdict
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
from pathlib import Path
from .uart_campaign import save, terminate
from .uart_reports import AttemptWindow
from . import uart_checkpoint as checkpoint
from .ssh_lifetime import restore as restore_checkpoint


def receiver_for(config, directory):
    if config.telemetry_mode == 'uart':
        from .uart_receiver import UARTReceiver
        return UARTReceiver(config.uart, directory)
    if config.telemetry_mode == 'ssh':
        from .ssh_capture import SSHReceiver
        return SSHReceiver(config.ssh, directory)
    raise ValueError('Unsupported transport')


def fingerprint(config, parents):
    if config.telemetry_mode == 'uart':
        return checkpoint.fingerprint(config, parents)
    value = dict(engine='dual-ssh-round-robin-v1', python=list(sys.version_info[:2]),
        parents=[p.digest for p in parents], mode='ssh', ssh=asdict(config.ssh),
        udc=config.udc, driver=config.gadget_udc_driver, seed=config.seed,
        iterations=config.iterations, seconds=config.seconds, pre=config.pre_run_quiet_seconds,
        post=config.post_run_capture_seconds, feedback=config.feedback, strategy=config.strategy)
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def run_campaign(config, seeds, *, stop=None, receiver_factory=None, command_factory=None,
                 lock_root=Path('/run/lock/usb-device-lab'), resume=None, skip=None):
    from .model import DeviceConfig
    from .mutator import Mutator
    if config.telemetry_mode not in ('uart', 'ssh') or config.feedback != 'none' or config.strategy != 'round-robin':
        raise ValueError('Choose UART or SSH, feedback=none and round-robin')
    if type(config.iterations) is not int or not 1 <= config.iterations <= 10000:
        raise ValueError('Campaign supports 1..10000 attempts')
    for value in (config.seconds, config.pre_run_quiet_seconds, config.post_run_capture_seconds):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 300:
            raise ValueError('Invalid campaign timing')
    if config.seconds == 0 or type(seeds) not in (list, tuple) or not 1 <= len(seeds) <= 128:
        raise ValueError('Supply positive duration and 1..128 seeds')
    parents = []
    for path in seeds:
        data = DeviceConfig.load(path).data
        data['udc_device'], data['udc_driver'] = config.udc, config.gadget_udc_driver
        parents.append(DeviceConfig(data))
    digest = fingerprint(config, parents)
    restored = restore_checkpoint(resume, config, parents, fingerprint) if resume else None
    first = restored['next_iteration'] if restored else 0
    if first == config.iterations:
        raise ValueError('Checkpoint campaign is already complete')
    mutator = Mutator(config.seed)
    if restored:
        mutator.random.setstate(restored['prng'])
    stop = stop if stop is not None else threading.Event()
    skip = skip if skip is not None else threading.Event()
    factory = receiver_factory or (lambda settings, path: receiver_for(config, path))
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
        if info.st_size:
            raise RuntimeError('UDC cleanup marker exists; inspect executor/stand first')
        ident = uuid.uuid4().hex
        root, capture = config.results_dir / ident, config.logs_dir / config.telemetry_mode / ident
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            boot = None
        save(root / 'campaign.json', dict(schema_version=2, mode=config.telemetry_mode,
            feedback='none', capture_id=ident, capture_directory=str(capture), boot_id=boot,
            seed=config.seed, explicit_seeds=[str(Path(p).resolve()) for p in seeds],
            iterations=config.iterations, seconds=config.seconds, udc=config.udc,
            udc_driver=config.gadget_udc_driver, resumed_from=restored['source_capture'] if restored else None,
            first_iteration=first, causal=False, coverage_valid=False,
            transport_lifetime_seconds=config.ssh.lease_seconds if config.telemetry_mode == 'ssh' else 0,
            ssh_lifetime_migrated_from=restored.get('ssh_lifetime_migrated_from') if restored else None))
        checkpoint.write(root / 'checkpoint.json', digest, first, mutator.random, ident)
        receiver = factory(config.capture_settings, capture)
        def receive():
            try:
                receiver.run()
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=receive, name='dual-capture', daemon=True)
        thread.start()
        def healthy():
            state = receiver.status()['state']
            if errors or not thread.is_alive() or state in ('failed', 'stopped'):
                raise RuntimeError('Capture receiver failed: ' + '; '.join(errors))
        deadline = time.monotonic() + 10
        while receiver.status()['state'] != 'connected':
            if stop.wait(0.02):
                break
            healthy()
            if time.monotonic() >= deadline:
                raise RuntimeError('Capture did not become ready within 10 seconds; no USB launch')
        def connected():
            healthy()
            if receiver.status()['state'] != 'connected':
                raise RuntimeError('Capture disconnected; campaign halted without automatic fallback')
        def pause(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                connected()
                if skip.is_set() or stop.wait(min(0.02, max(0, deadline - time.monotonic()))):
                    return False
            return not stop.is_set() and not skip.is_set()
        for iteration in range(first, config.iterations):
            if stop.is_set():
                break
            connected()
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
                if pause(config.pre_run_quiet_seconds) and not stop.is_set():
                    connected()
                    argv = command_factory(directory / 'config.json') if command_factory else [
                        sys.executable, '-u', '-m', 'usb_device_lab.device', str(directory / 'config.json')]
                    with (directory / 'executor.log').open('xb') as log:
                        active_start = time.monotonic_ns()
                        try:
                            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
                            deadline = time.monotonic() + config.seconds
                            while process.poll() is None and time.monotonic() < deadline:
                                connected()
                                if skip.is_set() or stop.wait(0.02):
                                    break
                            completed = process.poll() is None and not stop.is_set() and not skip.is_set() and time.monotonic() >= deadline
                        finally:
                            active_end = time.monotonic_ns()
                            if process is not None:
                                terminate(process)
                            log.flush(); os.fsync(log.fileno())
                    if process.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL) or not completed and not stop.is_set() and not skip.is_set():
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
                            raise OSError('Cannot persist cleanup marker')
                        marker = marker[written:]
                    os.fsync(fd)
                active_end = max(active_end, active_start)
                end = max(time.monotonic_ns(), active_end + 1)
                save(directory / 'window.json', asdict(AttemptWindow(directory.name, ident, start, end, active_start, active_end)))
                skipped = skip.is_set() and not stop.is_set() and fatal is None
                result = dict(iteration=iteration, window_completed=completed and not skipped, interrupted=stop.is_set(),
                    skipped=skipped, infrastructure_error=fatal, executor_returncode=process.returncode if process is not None else None,
                    capture_status=receiver.status(), mode=config.telemetry_mode, completeness='unknown',
                    coverage_valid=False, pcs=[], causal=False, capture_id=ident)
                save(directory / 'result.json', result); outcomes.append(result)
            if fatal:
                raise RuntimeError(fatal)
            if not stop.is_set():
                connected()
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                checkpoint.write(root / 'checkpoint.json', digest, iteration + 1, mutator.random, ident)
                if skipped:
                    skip.clear()
        return dict(campaign_id=ident, directory=str(root), outcomes=outcomes)
    finally:
        try:
            if receiver is not None:
                receiver.stop()
            if thread is not None:
                thread.join(timeout=config.capture_settings.read_timeout + 4)
                if thread.is_alive():
                    raise RuntimeError('Capture thread did not stop; inspect before retrying')
            if errors:
                raise RuntimeError('Capture receiver failed: ' + '; '.join(errors))
        finally:
            os.close(fd)
