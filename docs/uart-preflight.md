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

Separate CLI only; this commit does not automatically gate campaign/web startup
or change their behavior. Eight tests passed using real temporary directories,
synthetic sysfs symlinks and /dev/null metadata, including explicit no-open/no-
process/no-mkdir assertions. Full repository CI and physical UART/USB not tested.
