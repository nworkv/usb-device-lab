"""Read-only UDC speed metadata and rejection of known speed-ceiling conflicts."""
from pathlib import Path
import re

SUPPORTED_MODEL_SPEEDS = (1, 2, 3)
SPEED_NAMES = {1: 'low-speed', 2: 'full-speed', 3: 'high-speed', 5: 'super-speed', 6: 'super-speed-plus'}


def read_speed_attribute(udc, attribute):
    if attribute not in ('maximum_speed', 'current_speed'):
        raise ValueError('Unsupported UDC speed attribute')
    try:
        with (Path(udc) / attribute).open('rb') as stream:
            raw = stream.read(129)
    except FileNotFoundError:
        return dict(known=False, speed=None, name=None, error='missing')
    except OSError:
        return dict(known=False, speed=None, name=None, error='unreadable')
    if len(raw) > 128:
        return dict(known=False, speed=None, name=None, error='oversized')
    try:
        name = raw.decode('ascii').strip()
    except UnicodeDecodeError:
        return dict(known=False, speed=None, name=None, error='invalid_encoding')
    for speed, label in SPEED_NAMES.items():
        if name == label:
            return dict(known=True, speed=speed, name=label, error=None)
    return dict(known=False, speed=None, name=None, error='unknown' if name in ('', 'UNKNOWN', 'unknown') else 'unrecognized')


def udc_speed_report(udc):
    return dict(source='sysfs_metadata', maximum=read_speed_attribute(udc, 'maximum_speed'),
        current_at_inspection=read_speed_attribute(udc, 'current_speed'),
        software_supported_speeds=[dict(speed=speed, name=SPEED_NAMES[speed]) for speed in SUPPORTED_MODEL_SPEEDS],
        controller_supported_modes=None, hardware_checked=False, runtime_connected=False,
        note='Maximum is a reported ceiling, not proof of lower-speed support. Current speed is a snapshot, not an observation attributed to a campaign. No automatic fallback or USB 3 runtime support is enabled.')


def check_requested_speed(data, sys_root=Path('/sys')):
    if type(data) is not dict:
        raise ValueError('Device configuration must be an object')
    speed = data.get('speed', 2)
    if type(speed) is not int or speed not in SUPPORTED_MODEL_SPEEDS:
        raise ValueError('Requested speed is not implemented by the executor; no fallback')
    udc = data.get('udc_device')
    if type(udc) is not str or re.fullmatch(r'[A-Za-z0-9._@:-]{1,127}', udc) is None or udc in ('.', '..'):
        raise ValueError('Invalid UDC instance name for speed precheck')
    maximum = read_speed_attribute(Path(sys_root) / 'class' / 'udc' / udc, 'maximum_speed')
    within = speed <= maximum['speed'] if maximum['known'] else None
    if within is False:
        raise ValueError('Requested ' + SPEED_NAMES[speed] + ' exceeds UDC maximum ' + maximum['name'] + '; no fallback')
    return dict(requested_speed=speed, maximum=maximum, not_above_reported_maximum=within,
        controller_support_confirmed=False, hardware_checked=False)
