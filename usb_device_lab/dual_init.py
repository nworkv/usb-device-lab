"""Generate a validated TOML; no connections, boot edits or existing-file overwrite."""
import argparse
import json
import os
import re
from pathlib import Path
from .dual_config import from_dict
from .dual_preflight import udc_driver_name


def main(argv=None):
    p = argparse.ArgumentParser(description='Create agentless UART/SSH config')
    p.add_argument('--repo-dir', default=str(Path.home() / 'usb-device-lab'))
    p.add_argument('--config', default=str(Path.home() / '.config/usb-device-lab/lab.dual.toml'))
    p.add_argument('--state-dir', default=str(Path.home() / '.local/state/usb-device-lab'))
    p.add_argument('--mode', choices=('uart', 'ssh'), required=True)
    p.add_argument('--udc'); p.add_argument('--driver', help='Raw Gadget USB_UDC_NAME, not the Linux driver basename')
    p.add_argument('--uart-port'); p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--ssh-host'); p.add_argument('--ssh-user'); p.add_argument('--ssh-key'); p.add_argument('--known-hosts')
    p.add_argument('--ssh-direct', action='store_true', help='Read dmesg without sudo on host')
    p.add_argument('--lease-seconds', type=int, default=600)
    p.add_argument('--iterations', type=int, default=3)
    args = p.parse_args(argv)
    repo, state, config = map(lambda x: Path(x).expanduser().resolve(), (args.repo_dir, args.state_dir, args.config))
    if config.exists() or config.is_symlink():
        p.exit(2, 'Config already exists; edit deliberately, it was not overwritten\n')
    udcs = sorted(path.name for path in Path('/sys/class/udc').glob('*'))
    udc = args.udc or (udcs[0] if len(udcs) == 1 else None)
    if udc is None:
        p.exit(2, 'Specify --udc; cannot choose automatically\n')
    if re.fullmatch(r'[A-Za-z0-9._@:-]{1,127}', udc) is None or udc in ('.', '..'):
        p.exit(2, 'Invalid UDC instance name\n')
    driver = args.driver
    if driver is None:
        try:
            driver = udc_driver_name(Path('/sys/class/udc') / udc)
        except (OSError, ValueError) as error:
            p.exit(2, 'Cannot read USB_UDC_NAME; inspect uevent or specify --driver: ' + str(error) + '\n')
    raw = dict(gadget=dict(udc=udc, udc_driver=driver, device_config=str(repo / 'corpus/seeds/hid-keyboard.json'), profile='raspberry-pi-4'),
        telemetry=dict(schema_version=1, mode=args.mode),
        corpus=dict(seed_dir=str(repo / 'corpus/seeds'), manifest=str(repo / 'corpus/manifest.toml'), families=['hid'], profiles=['hid/keyboard'], strategy='round-robin'),
        campaign=dict(iterations=args.iterations, seconds=2, seed=42, feedback='none', pre_run_quiet_seconds=0.5, post_run_capture_seconds=1.0),
        run=dict(corpus_out=str(state / 'corpus'), results_dir=str(state / 'results'), logs_dir=str(state / 'logs')))
    if args.uart_port:
        raw['uart'] = dict(port=args.uart_port, baudrate=args.baud, data_bits=8, parity='none', stop_bits=1,
                           read_timeout=0.25, reconnect_delay=1.0, read_chunk_bytes=4096)
    ssh = (args.ssh_host, args.ssh_user, args.ssh_key, args.known_hosts)
    if any(ssh):
        if not all(ssh):
            p.exit(2, 'Specify all four SSH fields\n')
        raw['ssh'] = dict(host=args.ssh_host, user=args.ssh_user, identity_file=str(Path(args.ssh_key).expanduser().resolve()),
                         known_hosts_file=str(Path(args.known_hosts).expanduser().resolve()), use_sudo=not args.ssh_direct, lease_seconds=args.lease_seconds)
    from_dict(raw, config)
    # Validate both configured profiles, not just the initially selected one.
    for mode in ('uart', 'ssh'):
        if mode in raw:
            from_dict(raw, config, mode)
    lines = []
    for section, values in raw.items():
        lines.append('[' + section + ']')
        for key, value in values.items():
            lines.append(key + ' = ' + json.dumps(value, ensure_ascii=True))
        lines.append('')
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines)); f.flush(); os.fsync(f.fileno())
    print(str(config))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
