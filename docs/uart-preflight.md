# Read-only UART preflight

```sh
python3 -m usb_device_lab.uart_preflight --config examples/lab.uart.example.toml --seeds corpus/seeds/hid-keyboard.json
python3 -m unittest discover -s tests -p 'test_uart_preflight.py' -v
```

Linux metadata check only. No UART/Raw Gadget open, ioctl, subprocess, directory
creation, locks or writes. TOML is read by the existing strict loader. Checks:
UART character-node/read access, /dev/raw-gadget character-node/read-write access,
configured UDC directory, driver symlink basename match, writable output ancestors,
readable regular seed files, safe lock directory and absence of cleanup markers.

The sample's empty udc_driver falls back to the UDC device identifier, which is
not necessarily the driver name. If the match fails, inspect observed/configured
values and set the real driver explicitly; this command never edits the config.
The sysfs driver link may be missing/unavailable, producing a failed metadata
check rather than a claim that the hardware works.

Exit 0: all metadata checks passed. Exit 1: at least one failed. Exit 2: invalid
configuration/input or inspection error. hardware_checked and runtime_connected
always remain false. Even /dev/null meets the character-node criterion; tty
identity is NOT established without opening the port. UART speed/framing,
electrical levels, console output, host health, exclusive ownership, enumeration
and seed JSON validity remain unknown. This is not permission to blindly wire or
run the stand, and not a substitute for electrical/physical acceptance checks.

Permissions and filesystem metadata can change after inspection. Writable parent
checks do not reserve space or guarantee creation/storage success. A zero-size
lock file does not prove that no campaign holds its advisory lock. Trusted config
and parent paths are required. Device/sysfs symlinks are followed for metadata;
lock-directory/file symlinks are rejected through lstat checks. The command never
clears cleanup markers or starts/stops an existing campaign.

## Mandatory campaign CLI gate

python -m usb_device_lab.uart_campaign now runs this check after TOML loading and
before calling run_campaign, for both new campaigns and --resume. Failed metadata
prints its JSON report to stderr and exits 1 without campaign creation/device
opens. Loader/inspection errors exit 2. Cancellation during preflight returns 130
without starting a campaign. Success prints a metadata-only warning to stderr;
stdout retains the final campaign JSON. Signal handlers are restored on all paths.
No skip flag is provided. Passing checks still does NOT verify physical readiness.

This gate applies ONLY to the uart_campaign CLI main function. Direct Python
run_campaign calls and the separate uart_web supervisor still bypass it; their
behavior and PTY integration tests are unchanged. Do not assume web startup is
preflight-protected. The check does not validate a resume snapshot; normal
checkpoint validation still occurs inside run_campaign before hardware access.

The prior eight metadata tests passed using real temporary directories, synthetic
sysfs and /dev/null. Six new CLI-flow tests passed with injected loader/preflight
and campaign callable, covering rejection, resume forwarding, errors, strict pass
boolean and cancellation. They do not replace full-repository regression/CI or
physical UART/USB acceptance, neither of which was run for this change.

Run: python3 -m unittest discover -s tests -p 'test_uart_cli_preflight.py' -v
