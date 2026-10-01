#!/bin/sh
set -eu
host=${1:?usage: $0 HOST_IP UDC [USB_BUS]}
udc=${2:?usage: $0 HOST_IP UDC [USB_BUS]}
bus=${3:-1}
cat > lab.toml <<EOF
[gadget]
udc = "$udc"
device_config = "examples/composite.json"
profile = "raspberry-pi-4"

[host]
address = "$host"
ssh_user = "root"
usb_bus = $bus

[corpus]
seed_dir = "corpus/seeds"
strategy = "coverage-guided"

[run]
corpus_out = "var/corpus"
results_dir = "var/results"
logs_dir = "var/logs"
EOF
mkdir -p var/corpus var/results var/logs
python3 -m usb_device_lab.doctor gadget --udc "$udc"
printf '%s\n' 'lab.toml and var/ directories are ready'
