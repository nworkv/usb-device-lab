# USB Device Lab

USB Device Lab is a Linux USB host-stack testing lab. A gadget-side executor emulates USB devices through Raw Gadget; a host-side agent collects KCOV and kernel logs; the runner stores every invocation, coverage result and finding.

## Layout

- `usb_device_lab/`: executor, runner, protocol, mutator, host agent and storage
- `examples/`: device and lab configuration examples
- `corpus/seeds/`: starting USB-device profiles
- `var/corpus/`: coverage-increasing generated inputs
- `var/results/`: one directory per run
- `var/logs/`: gadget and host logs

## Prerequisites

Use two Linux machines connected by USB: the gadget machine needs a UDC supported by Raw Gadget and `/dev/raw-gadget`; the host needs debugfs, KCOV and `/dev/kmsg`. Python 3.11 or newer is required for TOML configuration.

## Quick start

```bash
git clone https://github.com/nworkv/usb-device-lab
cd usb-device-lab
cp examples/lab.example.toml lab.toml
```

Edit `lab.toml` with the gadget UDC, the host IPv4/IPv6 address, SSH user and USB bus number. Paths are resolved relative to `lab.toml`.

```toml
[gadget]
udc = "your-udc-name"
device_config = "examples/composite.json"

[host]
address = "192.168.1.20"
ssh_user = "root"
usb_bus = 1
```

Run non-invasive checks before a hardware run:

```bash
python -m usb_device_lab.doctor gadget --udc your-udc-name
python -m usb_device_lab.doctor host --bus 1
```

Initialize the configured output directories:

```bash
python -c 'from usb_device_lab.lab import load, create_directories; create_directories(load("lab.toml"))'
```

For a direct gadget smoke test, use the supplied device description:

```bash
sudo python -m usb_device_lab.device examples/composite.json
```

## Unified configuration

`lab.toml` is the source of truth for a run. `gadget` selects the UDC and device description; `host` tells the runner where to collect host-side coverage and logs; `corpus` selects seed families and scheduling; `run` sets corpus, result and log destinations.

The corpus manifest declares HID, Audio, CDC, Mass Storage, MIDI, UVC, composite and boundary-case families. Keep hand-written compatible profiles in `corpus/seeds/`; store coverage-improving generated cases in `var/corpus/`; preserve minimized reproducers next to their result in `var/results/`.

## Logs and findings

The gadget executor emits JSON events for control transfers, endpoint traffic and protocol errors. Host-side KCOV and kernel messages belong to the same run directory as the input and outcome. Never rely on console output alone: retain the corresponding run directory when reporting a finding.

## Safety

Run only against hosts and USB controllers you own or are authorized to test. Start with a dedicated host, a short time limit and a small seed subset. Use the `doctor` commands after each kernel, cabling or UDC change.
