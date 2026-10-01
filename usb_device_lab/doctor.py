import argparse
import gzip
import json
import os
import re
from pathlib import Path

RPI4_UDC='fe980000.usb'

def status(ok, detail):
    return {'ok': bool(ok), 'detail': detail}

def readlink_name(path):
    try:
        return Path(os.path.realpath(path)).name
    except OSError:
        return None

def read_text_or_none(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None

def gadget_report(udc=None):
    nodes = sorted(Path('/sys/class/udc').glob('*'))
    names = [x.name for x in nodes]
    selected = udc or (names[0] if len(names) == 1 else None)
    report = {'role': 'gadget', 'raw_gadget': status(Path('/dev/raw-gadget').exists(), '/dev/raw-gadget'), 'udcs': names, 'selected_udc': selected}
    if selected:
        node = Path('/sys/class/udc') / selected
        report['udc'] = status(node.exists(), str(node))
        if node.exists():
            report['udc_driver'] = readlink_name(node / 'device' / 'driver')
            report['udc_state'] = read_text_or_none(node / 'state')
    else:
        report['udc'] = status(False, 'select --udc when zero or multiple UDCs exist')
    report['ready'] = report['raw_gadget']['ok'] and report['udc']['ok']
    return report

def host_report(bus=None):
    debug = Path('/sys/kernel/debug')
    kcov = debug / 'kcov'
    report = {'role': 'host', 'debugfs': status(debug.is_dir(), str(debug)), 'kcov': status(kcov.exists(), str(kcov)), 'bus': bus}
    if bus is not None:
        report['bus'] = status(1 <= bus <= 255, 'USB bus must be 1..255')
    report['kmsg'] = status(Path('/dev/kmsg').exists(), '/dev/kmsg')
    report['ready'] = report['debugfs']['ok'] and report['kcov']['ok'] and report['kmsg']['ok'] and (bus is None or report['bus']['ok'])
    return report

# ---------------------------------------------------------------- Raspberry Pi 4
def _read_bytes(path):
    try:
        return path.read_bytes()
    except OSError:
        return None

def kernel_config_option(option, root=Path('/'), release=None):
    """Return ('y'|'m'|'n'|None, source). None means no kernel config was found."""
    release = release or os.uname().release
    pattern = re.compile(r'^' + re.escape(option) + r'=(y|m)$|^# ' + re.escape(option) + r' is not set$', re.M)
    candidates = [(root / 'proc/config.gz', True), (root / 'boot' / f'config-{release}', False),
                  (root / 'lib/modules' / release / 'build/.config', False)]
    for path, compressed in candidates:
        data = _read_bytes(path)
        if data is None:
            continue
        try:
            text = (gzip.decompress(data) if compressed else data).decode(errors='replace')
        except OSError:
            continue
        m = pattern.search(text)
        return (m.group(1) or 'n') if m else 'n', str(path)
    module = option.removeprefix('CONFIG_USB_').lower()
    if (root / 'sys/module' / module).exists():
        return 'm', str(root / 'sys/module' / module)
    builtin = _read_bytes(root / 'lib/modules' / release / 'modules.builtin')
    if builtin and f'/{module}.ko'.encode() in builtin:
        return 'y', 'modules.builtin'
    return None, 'no /proc/config.gz, /boot/config-*, or module information'

def _dr_mode(root):
    dt = root / 'proc/device-tree'
    for path in [dt / 'soc/usb@7e980000/dr_mode', *sorted(dt.glob('**/usb@*980000/dr_mode'))]:
        data = _read_bytes(path)
        if data is not None:
            return data.rstrip(b'\0').decode(errors='replace'), str(path)
    return None, 'dr_mode not found in device tree'

def _config_txt(root):
    for path in (root / 'boot/firmware/config.txt', root / 'boot/config.txt'):
        data = _read_bytes(path)
        if data is None:
            continue
        for line in data.decode(errors='replace').splitlines():
            line = line.split('#', 1)[0].strip()
            if re.match(r'^dtoverlay=dwc2(,|$)', line):
                return True, f'{path}: {line}'
        return False, f'{path}: no dtoverlay=dwc2 (add "dtoverlay=dwc2,dr_mode=peripheral" under [all])'
    return False, 'config.txt not found'

def rpi4_report(udc=None, root=Path('/'), release=None):
    """Pre-campaign checks for a Raspberry Pi 4 used as the gadget via its USB-C port."""
    root = Path(root)
    checks = {}
    model = (_read_bytes(root / 'proc/device-tree/model') or b'').rstrip(b'\0').decode(errors='replace')
    checks['model'] = status('Raspberry Pi 4' in model or 'Compute Module 4' in model, model or 'unknown board')
    ok, detail = _config_txt(root)
    checks['dwc2_overlay'] = status(ok, detail)
    mode, source = _dr_mode(root)
    checks['dr_mode'] = status(mode in ('peripheral', 'otg'), f'{mode} ({source})' if mode else source)
    udc_dir = root / 'sys/class/udc'
    names = sorted(x.name for x in udc_dir.glob('*')) if udc_dir.is_dir() else []
    selected = udc or (RPI4_UDC if RPI4_UDC in names else (names[0] if len(names) == 1 else None))
    checks['usb_c_udc'] = status(selected == RPI4_UDC and selected in names,
                                 f'{selected or "none"} present; expected {RPI4_UDC} (USB-C port)' if selected in names else
                                 f'UDC {RPI4_UDC} missing; found {names or "none"}: enable dwc2 overlay and reboot')
    driver = readlink_name(udc_dir / selected / 'device' / 'driver') if selected in names else None
    checks['dwc2_driver'] = status(driver == 'dwc2', f'UDC driver {driver or "none"}; expected dwc2 (modprobe dwc2)')
    if selected in names:
        state = read_text_or_none(udc_dir / selected / 'state')
        checks['udc_state'] = status(True, f'{state or "unknown"} (not attached until a gadget binds)')
    raw = root / 'dev/raw-gadget'
    checks['raw_gadget_node'] = status(raw.exists(), f'{raw} ' + ('present' if raw.exists() else 'missing: sudo modprobe raw_gadget'))
    if raw.exists():
        checks['raw_gadget_access'] = status(os.access(raw, os.R_OK | os.W_OK), 'read/write access (run fuzz/replay with sudo)')
    value, source = kernel_config_option('CONFIG_USB_RAW_GADGET', root, release)
    checks['CONFIG_USB_RAW_GADGET'] = status(value in ('y', 'm'), f'{value or "unknown"} from {source}')
    required = ('model', 'dr_mode', 'usb_c_udc', 'dwc2_driver', 'raw_gadget_node', 'CONFIG_USB_RAW_GADGET')
    ready = all(checks[k]['ok'] for k in required)
    return {'role': 'rpi4', 'udc': selected, 'checks': checks, 'ready': ready,
            'advice': None if ready else 'Pi 4 gadget mode: dtoverlay=dwc2,dr_mode=peripheral in config.txt, '
                      'modules dwc2 and raw_gadget loaded, power the Pi via GPIO/PoE when USB-C is the data link.'}

def main(argv=None):
    parser = argparse.ArgumentParser(description='USB Device Lab non-invasive environment checks')
    sub = parser.add_subparsers(dest='role', required=True)
    gadget = sub.add_parser('gadget')
    gadget.add_argument('--udc')
    host = sub.add_parser('host')
    host.add_argument('--bus', type=int)
    rpi = sub.add_parser('rpi4', help='Raspberry Pi 4 DWC2/USB-C/Raw Gadget checks')
    rpi.add_argument('--udc')
    args = parser.parse_args(argv)
    if args.role == 'gadget':
        report = gadget_report(args.udc)
    elif args.role == 'host':
        report = host_report(args.bus)
    else:
        report = rpi4_report(args.udc)
    print(json.dumps(report, indent=2))
    return 0 if report['ready'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
