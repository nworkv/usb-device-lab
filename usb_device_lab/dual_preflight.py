"""Metadata-only checks; no SSH connection, device open or boot modifications."""
import hashlib
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from .uart_preflight import access, character, writable_ancestor


def inspect(config, seeds, *, sys_root=Path('/sys'), raw_path=Path('/dev/raw-gadget'),
            lock_root=Path('/run/lock/usb-device-lab')):
    if config.telemetry_mode not in ('uart', 'ssh') or re.fullmatch(r'[A-Za-z0-9._@:-]{1,127}', config.udc) is None or config.udc in ('.', '..'):
        raise ValueError('Invalid mode or UDC')
    if not seeds or len(seeds) > 128:
        raise ValueError('Supply 1..128 seeds')
    checks = []
    def add(name, passed, **details):
        checks.append(dict(name=name, passed=bool(passed), details=details))
    add('linux', sys.platform.startswith('linux'))
    add('raw_gadget_character_node_accessible', character(raw_path, os.R_OK | os.W_OK), path=str(raw_path))
    udc = Path(sys_root) / 'class' / 'udc' / config.udc
    add('udc_directory_present', udc.is_dir(), path=str(udc))
    actual = None
    try:
        driver = udc / 'device' / 'driver'
        if driver.is_symlink():
            actual = driver.resolve(strict=True).name
    except (OSError, RuntimeError):
        pass
    add('udc_driver_matches', actual is not None and actual == config.gadget_udc_driver, observed=actual)
    if config.telemetry_mode == 'uart':
        from .uart_receiver import check_settings
        check_settings(config.uart)
        add('uart_character_node_readable', character(config.uart.port, os.R_OK), path=config.uart.port)
    else:
        add('ssh_client_available', shutil.which('ssh') is not None)
        for name, private in (('identity_file', True), ('known_hosts_file', False)):
            path = Path(getattr(config.ssh, name))
            try:
                info = path.lstat()
                safe = stat.S_ISREG(info.st_mode) and access(path, os.R_OK) and not info.st_mode & (0o077 if private else 0o022)
                safe = safe and (info.st_uid == os.geteuid() or os.geteuid() == 0)
            except OSError:
                safe = False
            add('ssh_' + name + '_safe_readable', safe, path=str(path))
    for name in ('results_dir', 'logs_dir', 'corpus_out'):
        passed, ancestor = writable_ancestor(getattr(config, name))
        add(name + '_writable_ancestor', passed, ancestor=ancestor)
    for number, seed in enumerate(seeds):
        path = Path(seed)
        try:
            info = path.stat()
            passed = stat.S_ISREG(info.st_mode) and access(path, os.R_OK)
        except OSError:
            passed = False
        add('seed_%d_readable' % number, passed)
    lock_root = Path(lock_root)
    try:
        info = lock_root.lstat()
        add('lock_directory_safe', stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022)
    except FileNotFoundError:
        passed, ancestor = writable_ancestor(lock_root.parent)
        add('lock_parent_writable', passed, ancestor=ancestor)
    except OSError:
        add('lock_directory_safe', False)
    lock = lock_root / (hashlib.sha256(config.udc.encode()).hexdigest() + '.lock')
    try:
        info = lock.lstat()
        add('no_cleanup_marker', stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_size == 0)
    except FileNotFoundError:
        add('no_cleanup_marker', True)
    except OSError:
        add('no_cleanup_marker', False)
    return dict(schema_version=2, mode=config.telemetry_mode, checks=checks,
        metadata_checks_passed=all(c['passed'] for c in checks), hardware_checked=False, runtime_connected=False,
        unknown=['electrical_levels', 'console_configuration', 'SSH_authentication_and_remote_log_permissions',
                 'host_health', 'exclusive_UDC_ownership', 'usb_enumeration', 'capture_completeness'],
        note='Metadata only. Mode selection is not hardware or network acceptance.')
