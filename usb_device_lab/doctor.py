import argparse
import json
import os
from pathlib import Path

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

def main(argv=None):
    parser = argparse.ArgumentParser(description='USB Device Lab non-invasive environment checks')
    sub = parser.add_subparsers(dest='role', required=True)
    gadget = sub.add_parser('gadget')
    gadget.add_argument('--udc')
    host = sub.add_parser('host')
    host.add_argument('--bus', type=int)
    args = parser.parse_args(argv)
    report = gadget_report(args.udc) if args.role == 'gadget' else host_report(args.bus)
    print(json.dumps(report, indent=2))
    return 0 if report['ready'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
