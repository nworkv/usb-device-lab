#!/bin/sh
# Raspberry Pi 4 bootstrap: write lab.toml via the lab CLI and run local checks.
set -eu
host=${1:?usage: $0 HOST_IP [UDC] [USB_BUS] [SSH_USER]}
udc=${2:-fe980000.usb}
bus=${3:-1}
user=${4:-root}
python3 -m usb_device_lab.lab init --host "$host" --udc "$udc" --bus "$bus" --ssh-user "$user" --profile raspberry-pi-4 --force
python3 -m usb_device_lab.lab check --config lab.toml --local-only || {
    echo 'Local checks failed; see FAIL lines above (dtoverlay=dwc2,dr_mode=peripheral, modprobe dwc2 raw_gadget).' >&2
    exit 1
}
echo 'lab.toml ready. Next: python3 -m usb_device_lab.lab check --config lab.toml'
