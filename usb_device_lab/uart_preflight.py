"""Read-only filesystem preflight; never opens UART/Raw Gadget or runs processes."""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


def access(path, mode):
    return os.access(path, mode, effective_ids=True)


def character(path, mode):
    try:
        info = Path(path).stat()
        return stat.S_ISCHR(info.st_mode) and access(path, mode)
    except OSError:
        return False


def writable_ancestor(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return path.is_dir() and access(path, os.W_OK | os.X_OK), str(path)
    except OSError:
        return False, str(path)


def inspect(config, seeds, *, sys_root=Path('/sys'), raw_path=Path('/dev/raw-gadget'),
            lock_root=Path('/run/lock/usb-device-lab')):
    if config.telemetry_mode != 'uart' or type(config.udc) is not str or re.fullmatch(r'[A-Za-z0-9._@:-]{1,127}', config.udc) is None or config.udc in ('.', '..'):
        raise ValueError('Invalid UART mode or UDC name')
    if type(seeds) not in (list, tuple) or not 1 <= len(seeds) <= 128:
        raise ValueError('Supply explicit campaign seeds')
    checks = []
    def add(name, passed, **details):
        checks.append(dict(name=name, passed=bool(passed), details=details))
    add('linux', sys.platform.startswith('linux'))
    add('uart_character_node_readable', character(config.uart.port, os.R_OK), path=config.uart.port)
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
    add('udc_driver_matches', actual is not None and actual == config.gadget_udc_driver,
        configured=config.gadget_udc_driver, observed=actual)
    for name in ('results_dir', 'logs_dir'):
        passed, ancestor = writable_ancestor(getattr(config, name))
        add(name + '_writable_ancestor', passed, requested=str(getattr(config, name)), ancestor=ancestor)
    for number, seed in enumerate(seeds):
        path = Path(seed)
        try:
            info = path.stat()
            passed = stat.S_ISREG(info.st_mode) and access(path, os.R_OK)
        except OSError:
            passed = False
        add('seed_file_%d_readable' % number, passed, path=str(path))
    lock_root = Path(lock_root)
    try:
        info = lock_root.lstat()
        safe = stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022
        add('lock_directory_safe', safe, path=str(lock_root))
    except FileNotFoundError:
        passed, ancestor = writable_ancestor(lock_root.parent)
        add('lock_parent_writable', passed, ancestor=ancestor)
    except OSError:
        add('lock_directory_safe', False, path=str(lock_root))
    lock = lock_root / (hashlib.sha256(config.udc.encode()).hexdigest() + '.lock')
    try:
        info = lock.lstat()
        clean = stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_size == 0
        add('no_cleanup_marker', clean, path=str(lock))
    except FileNotFoundError:
        add('no_cleanup_marker', True, path=str(lock), note='No marker file observed')
    except OSError:
        add('no_cleanup_marker', False, path=str(lock))
    return dict(schema_version=1, metadata_checks_passed=all(c['passed'] for c in checks),
        hardware_checked=False, runtime_connected=False, checks=checks,
        unknown=['tty_identity', 'uart_console_speed_and_framing', 'electrical_levels',
                 'host_health', 'exclusive_port_or_udc_ownership', 'usb_enumeration', 'seed_json_schema'],
        note='Metadata only; resources can change after inspection. Do not treat this as hardware readiness.')


def main(argv=None):
    from .uart_config import load
    parser = argparse.ArgumentParser(description='Read-only UART stand preflight; no device opens')
    parser.add_argument('--config', required=True)
    parser.add_argument('--seeds', nargs='+', required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(load(args.config), args.seeds)
    except (OSError, ValueError) as error:
        parser.exit(2, 'Preflight error: ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['metadata_checks_passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
