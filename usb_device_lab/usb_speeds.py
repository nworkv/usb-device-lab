"""Read-only UDC speed metadata; a reported ceiling is not a mode capability list."""
from pathlib import Path

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
