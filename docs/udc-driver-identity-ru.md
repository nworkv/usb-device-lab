# Raw Gadget: UDC instance, API driver name and Linux driver

For the verified Pi 4 stand:

```text
/sys/class/udc/fe980000.usb/uevent: USB_UDC_NAME=fe980000.usb
/sys/class/udc/fe980000.usb/device/driver: basename dwc2
```

Use `[gadget] udc="fe980000.usb"` and `udc_driver="fe980000.usb"`.
The Linux basename dwc2 is diagnostic information, not Raw Gadget driver_name.
Other controllers must use their actual USB_UDC_NAME; no board-specific fallback.

The unified preflight and generator now read the bounded uevent field. Missing,
duplicate, malformed or unreadable values fail closed. Preflight exposes configured,
observed, linux_driver, source and identity_error separately. No device open or
network connection is added. The existing operator config is never rewritten.

If your existing config already has these verified values, leave it unchanged.
After pulling this commit, stop/restart the panel and repeat:

```sh
sudo -- "$HOME/usb-device-lab/.venv-uart/bin/python" -m usb_device_lab.dual_cli   --config "$HOME/.config/usb-device-lab/lab.dual.toml" --mode ssh check
```

For a config previously generated with dwc2, verify the controller's uevent first,
back up the TOML and edit only gadget.udc_driver while the campaign is stopped.
Recheck before USB activity. Fingerprint includes driver: changing it invalidates
old checkpoints. Do not disable preflight or delete cleanup markers to bypass this.

Scope: dual_preflight, dual_init and the dual example; legacy uart_preflight and
pi_uart_setup are not changed. Use the unified dual entry point for this fix.
Tests cover identity parsing, both mode paths with fixture UART settings, mismatch,
missing identity and generator auto-detection. Isolated regression passed; full
checkout CI and physical USB/SSH/UART execution have not been verified here.
