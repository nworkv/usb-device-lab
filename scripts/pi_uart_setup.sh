#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
HOME="${HOME:-}"
REPO="$HOME/usb-device-lab"
URL="https://github.com/nworkv/usb-device-lab.git"
BRANCH="feature/uart-agentless"
CONFIG="$HOME/.config/usb-device-lab/lab.uart.toml"
STATE="$HOME/.local/state/usb-device-lab"
UART=""; BAUD=""; UDC=""; DRIVER=""; PORT=8766; ITERATIONS=3
INSTALL_DEPS=0; LOAD_MODULES=0; SERVE=0; REPLACE_CONFIG=0
LOG=""
usage() {
cat <<'HELP'
Usage: bash pi_uart_setup.sh --uart-port /dev/serial/by-id/ADAPTER --baud 115200 [options]
Required: --uart-port PATH --baud INTEGER (actual host settings, not auto-detected)
Options:
  --repo-dir PATH     Default ~/usb-device-lab
  --repo-url URL      Git credential helper/SSH URL; never a PAT in a URL
  --config PATH       Default ~/.config/usb-device-lab/lab.uart.toml
  --state-dir PATH    Default ~/.local/state/usb-device-lab
  --udc NAME          Explicit UDC when automatic selection is ambiguous
  --driver NAME       Explicit driver; otherwise sysfs driver basename
  --iterations N      Initial budget (default 3; 1..10000)
  --port N            Loopback panel port (default 8766)
  --install-deps      Allow sudo apt-get dependency installation
  --load-modules      Allow sudo modprobe dwc2/raw_gadget on identified Pi 4
  --replace-config    Backup and replace differing existing config
  --serve             Run PANEL as root in foreground after checks
  --help              Show help; no changes
No boot edits, reboot, device opens or automatic USB campaign. New config is 8N1.
Read and trust script/repository/venv before privileged execution. Protect token logs.
HELP
}
fail() { printf '\nERROR: %s\n' "$*" >&2; [[ -z "$LOG" ]] || printf 'Log: %s\n' "$LOG" >&2; exit 1; }
trap 'printf "\nFailed at line %s; exit %s\n" "$LINENO" "$?" >&2' ERR
while (($#)); do
  case "$1" in
    --uart-port|--baud|--repo-dir|--repo-url|--config|--state-dir|--udc|--driver|--iterations|--port)
      (($# >= 2)) || fail "Missing value for $1"
      key="$1"; value="$2"; shift 2
      case "$key" in
        --uart-port) UART="$value";; --baud) BAUD="$value";;
        --repo-dir) REPO="$value";; --repo-url) URL="$value";;
        --config) CONFIG="$value";; --state-dir) STATE="$value";;
        --udc) UDC="$value";; --driver) DRIVER="$value";;
        --iterations) ITERATIONS="$value";; --port) PORT="$value";;
      esac;;
    --install-deps) INSTALL_DEPS=1; shift;; --load-modules) LOAD_MODULES=1; shift;;
    --serve) SERVE=1; shift;; --replace-config) REPLACE_CONFIG=1; shift;;
    --help|-h) usage; exit 0;; *) fail "Unknown argument: $1";;
  esac
done
[[ -n "$HOME" ]] || fail "HOME missing; use ordinary operator login shell"
[[ $(uname -s) == Linux ]] || fail "Linux required"
[[ $EUID != 0 ]] || fail "Run as ordinary operator, NOT sudo bash; sudo is used selectively"
[[ -n "$UART" && -n "$BAUD" ]] || { usage; fail "Explicit --uart-port and --baud required"; }
[[ "$UART" == /dev/* && "$UART" != *'*'* && "$UART" != *'?'* ]] || fail "Exact /dev UART path required"
[[ "$BAUD" =~ ^[0-9]+$ && ${#BAUD} -le 7 ]] || fail "Invalid baudrate"
[[ "$ITERATIONS" =~ ^[0-9]+$ && ${#ITERATIONS} -le 5 ]] || fail "Invalid iterations"
[[ "$PORT" =~ ^[0-9]+$ && ${#PORT} -le 5 ]] || fail "Invalid port"
((10#$BAUD >= 50 && 10#$BAUD <= 4000000)) || fail "Baudrate outside config range"
((10#$ITERATIONS >= 1 && 10#$ITERATIONS <= 10000)) || fail "Iterations must be 1..10000"
((10#$PORT >= 1024 && 10#$PORT <= 65535)) || fail "Port must be 1024..65535"
for path in "$REPO" "$CONFIG" "$STATE"; do [[ "$path" == /* ]] || fail "Absolute path required: $path"; done
[[ ! "$URL" =~ ^https?://[^/]*@ ]] || fail "Do not embed HTTP credentials in logged URL; use Git helpers/SSH"
[[ "$REPO" != / && "$STATE" != / && "$CONFIG" != / ]] || fail "Unsafe root path"
mkdir -p "$STATE/setup-logs"
LOG="$STATE/setup-logs/$(date +%Y%m%d-%H%M%S)-$$.log"
exec > >(tee -a "$LOG") 2>&1
uname -a
printf 'Operator: %s\nRepo: %s\nConfig: %s\nState: %s\n' "$(id -un)" "$REPO" "$CONFIG" "$STATE"
if ((INSTALL_DEPS)); then
  command -v apt-get >/dev/null || fail "Automatic dependencies require Debian/Raspberry Pi OS apt-get"
  sudo apt-get update
  sudo apt-get install -y git python3 python3-venv python3-pip python3-full nodejs
fi
for tool in git python3 node sudo; do command -v "$tool" >/dev/null || fail "Missing $tool; install dependencies"; done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || fail "Python 3.11+ required; no OS upgrade is automated"
if [[ ! -e "$REPO" ]]; then
  git clone --branch "$BRANCH" --single-branch -- "$URL" "$REPO" || fail "Private GitHub access failed; configure credentials/SSH, not token URLs"
else
  [[ -d "$REPO/.git" ]] || fail "Existing directory is not a Git checkout"
  [[ -z $(git -C "$REPO" status --porcelain --untracked-files=no) ]] || fail "Tracked changes present; not discarding them"
  [[ $(git -C "$REPO" branch --show-current) == "$BRANCH" ]] || fail "Switch explicitly to $BRANCH first"
  git -C "$REPO" pull --ff-only
fi
printf 'Revision: '; git -C "$REPO" rev-parse HEAD
for file in usb_device_lab/uart_pipeline.py usb_device_lab/uart_pipeline_web.py scripts/run_uart_tests.py scripts/check_uart_checkpoint_source.py; do
  [[ -f "$REPO/$file" ]] || fail "Checkout lacks $file"
done
VENV="$REPO/.venv-uart"
[[ ! -L "$VENV" ]] || fail "Venv must not be a symlink"
python3 -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install --upgrade pip setuptools wheel
"$PY" -m pip install -e "$REPO"
cd "$REPO"
"$PY" -m compileall -q usb_device_lab tests scripts
"$PY" scripts/check_uart_checkpoint_source.py
"$PY" scripts/run_uart_tests.py --output "$STATE/setup-logs/uart-tests-$$.json"
"$PY" - <<'PY'
import subprocess
from usb_device_lab.uart_pipeline_ui import SCRIPT
from usb_device_lab.uart_review_http import SCRIPT as REVIEW
subprocess.run(['node','--check'],input=SCRIPT,check=True)
subprocess.run(['node','--check'],input=REVIEW,check=True)
PY
if ((LOAD_MODULES)); then
  [[ -r /proc/device-tree/model ]] || fail "Cannot identify board for module automation"
  model=$(tr -d '\0' </proc/device-tree/model)
  [[ "$model" == *"Raspberry Pi 4"* ]] || fail "Automatic dwc2 profile supports identified Pi 4 only"
  sudo modprobe dwc2 || fail "dwc2 unavailable; configure controller/kernel manually"
  sudo modprobe raw_gadget || fail "raw_gadget unavailable; kernel support/module required"
fi
[[ -d /sys/class/udc ]] || fail "No UDC class; configure gadget mode/kernel manually"
mapfile -t UDCS < <(find /sys/class/udc -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
if [[ -z "$UDC" ]]; then
  ((${#UDCS[@]} == 1)) || fail "Found ${#UDCS[@]} UDCs; configure board or specify --udc"
  UDC="${UDCS[0]}"
fi
[[ "$UDC" =~ ^[A-Za-z0-9._@:-]{1,127}$ && "$UDC" != . && "$UDC" != .. ]] || fail "Invalid UDC"
[[ -d "/sys/class/udc/$UDC" ]] || fail "UDC not present"
if [[ -z "$DRIVER" ]]; then
  target=$(readlink -f "/sys/class/udc/$UDC/device/driver") || fail "Cannot determine driver; inspect sysfs and specify --driver"
  [[ -d "$target" ]] || fail "Driver target unavailable"
  DRIVER=$(basename "$target")
fi
[[ "$DRIVER" =~ ^[A-Za-z0-9._@:-]{1,127}$ ]] || fail "Invalid driver"
[[ -c /dev/raw-gadget ]] || fail "Raw Gadget character device missing"
[[ -c "$UART" ]] || fail "UART character device missing: $UART"
printf 'UDC: %s\nDriver: %s\nUART: %s @ %s\n' "$UDC" "$DRIVER" "$UART" "$BAUD"
"$PY" - "$CONFIG" "$REPO" "$STATE" "$UART" "$BAUD" "$UDC" "$DRIVER" "$ITERATIONS" "$REPLACE_CONFIG" <<'PY'
import json,os,sys,time
from pathlib import Path
from usb_device_lab.uart_config import load
config,repo,state,port,baud,udc,driver,count,replace=sys.argv[1:]
p=Path(config);repo=Path(repo);state=Path(state)
if p.exists():
    old=load(p)
    same=(old.udc==udc and old.gadget_udc_driver==driver and old.uart.port==port and old.uart.baudrate==int(baud) and old.iterations==int(count))
    if not same and replace!='1':
        raise SystemExit('Existing config differs; edit deliberately or use --replace-config with backup')
    if same:
        print('Reusing existing config without modifying other settings')
        raise SystemExit(0)
    backup=p.with_name(p.name+'.backup-'+str(time.time_ns()))
    backup.write_bytes(p.read_bytes());backup.chmod(0o600)
q=lambda value:json.dumps(str(value))
text='\n'.join([
'[gadget]','udc = '+q(udc),'udc_driver = '+q(driver),'device_config = '+q(repo/'corpus/seeds/hid-keyboard.json'),'profile = "raspberry-pi-4"',
'[telemetry]','schema_version = 1','mode = "uart"',
'[uart]','port = '+q(port),'baudrate = '+str(int(baud)),'data_bits = 8','parity = "none"','stop_bits = 1','read_timeout = 0.25','reconnect_delay = 1.0','read_chunk_bytes = 4096',
'[corpus]','seed_dir = '+q(repo/'corpus/seeds'),'manifest = '+q(repo/'corpus/manifest.toml'),'families = ["hid"]','profiles = ["hid/keyboard"]','strategy = "round-robin"',
'[campaign]','iterations = '+str(int(count)),'seconds = 2','seed = 42','feedback = "none"','pre_run_quiet_seconds = 0.5','post_run_capture_seconds = 1.0',
'[run]','corpus_out = '+q(state/'corpus'),'results_dir = '+q(state/'results'),'logs_dir = '+q(state/'logs'),''])
p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
temporary=p.with_name(p.name+'.new-'+str(os.getpid()))
with temporary.open('x',encoding='utf-8') as f:
    f.write(text);f.flush();os.fsync(f.fileno())
temporary.chmod(0o600)
load(temporary)
os.replace(temporary,p)
print('Created config: HID keyboard, '+str(int(count))+' attempts, 8N1. Verify framing matches host.')
PY
"$PY" -m usb_device_lab.uart_config --config "$CONFIG"
"$PY" - "$CONFIG" <<'PY'
import sys
from usb_device_lab.uart_config import load
from usb_device_lab.uart_pipeline import select_seeds
for row in select_seeds(load(sys.argv[1])):print('Selected:',row['profile'],row['path'])
PY
mapfile -t SEEDS < <("$PY" - "$CONFIG" <<'PY'
import sys
from usb_device_lab.uart_config import load
from usb_device_lab.uart_pipeline import select_seeds
for row in select_seeds(load(sys.argv[1])):print(row['path'])
PY
)
((${#SEEDS[@]} > 0)) || fail "No seeds selected"
# Metadata inspected for the same root identity used by --serve; no device open.
sudo -- "$PY" -m usb_device_lab.uart_preflight --config "$CONFIG" --seeds "${SEEDS[@]}"
printf '\nSoftware/metadata checks passed; ELECTRICAL/HOST/USB readiness remains unknown.\n'
printf 'Config: %s\nTests/logs: %s\n' "$CONFIG" "$STATE/setup-logs"
if ((SERVE)); then
  printf 'Panel only, no automatic campaign. Open http://127.0.0.1:%s; protect printed token and setup-log.\n' "$PORT"
  exec sudo -- "$PY" -m usb_device_lab.uart_guarded_web --config "$CONFIG" --port "$PORT"
else
  printf 'Panel command:\n'
  printf 'sudo -- %q -m usb_device_lab.uart_guarded_web --config %q --port %q\n' "$PY" "$CONFIG" "$PORT"
fi
