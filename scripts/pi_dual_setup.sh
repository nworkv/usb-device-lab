#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
REPO="${USB_DEVICE_LAB_REPO:-$HOME/usb-device-lab}"
CONFIG="$HOME/.config/usb-device-lab/lab.dual.toml"
INSTALL=0; SERVE=0; ARGS=()
while (($#)); do
  case "$1" in
    --install-deps) INSTALL=1; shift;;
    --serve) SERVE=1; shift;;
    --repo-dir|--config)
      (($#>=2)) || { echo 'Missing value' >&2; exit 2; }
      if [[ "$1" == --repo-dir ]]; then REPO="$2"; else CONFIG="$2"; fi
      shift 2;;
    *) ARGS+=("$1"); shift;;
  esac
done
[[ $EUID != 0 ]] || { echo 'Run as ordinary operator, NOT sudo bash' >&2; exit 2; }
[[ "$REPO" == /* && "$CONFIG" == /* ]] || { echo 'Absolute repo/config paths required' >&2; exit 2; }
[[ -f "$REPO/usb_device_lab/dual_cli.py" ]] || { echo 'Apply dual-transport patch to checkout first' >&2; exit 2; }
if ((INSTALL)); then
  sudo apt-get update
  sudo apt-get install -y git python3 python3-venv python3-pip nodejs openssh-client
fi
for tool in python3 node sudo; do command -v "$tool" >/dev/null; done
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
[[ ! -L "$REPO/.venv-uart" ]] || { echo 'Venv cannot be a symlink' >&2; exit 2; }
python3 -m venv "$REPO/.venv-uart"
PY="$REPO/.venv-uart/bin/python"
"$PY" -m pip install -e "$REPO"
cd "$REPO"
"$PY" -m compileall -q usb_device_lab tests scripts
"$PY" scripts/check_uart_checkpoint_source.py
mkdir -p "$HOME/.local/state/usb-device-lab/setup-logs"
"$PY" scripts/run_uart_tests.py --output "$HOME/.local/state/usb-device-lab/setup-logs/dual-uart-regression-$$.json"
"$PY" -m unittest discover -s tests -p 'test_dual_*.py' -v
"$PY" - <<'PY'
import subprocess
from usb_device_lab.dual_ui import SCRIPT
subprocess.run(['node', '--check'], input=SCRIPT, check=True)
PY
if [[ -e "$CONFIG" || -L "$CONFIG" ]]; then
  echo 'Existing configuration preserved; generation arguments are ignored.'
else
  "$PY" -m usb_device_lab.dual_init --repo-dir "$REPO" --config "$CONFIG" "${ARGS[@]}"
fi
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CONFIG" check
if ((SERVE)); then
  exec sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CONFIG" serve --port 8766
fi
printf 'Panel command: sudo -- %q -m usb_device_lab.dual_cli --config %q serve --port 8766\n' "$PY" "$CONFIG"
