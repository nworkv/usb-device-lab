# USB speed modes

The normal device model and campaign executor accept Linux speed enum values
1 (Low Speed), 2 (Full Speed, the default), and 3 (High Speed). The normal
allowlist is usb_device_lab.usb_speeds.SUPPORTED_MODEL_SPEEDS. Values 5 and 6
remain rejected by normal DeviceConfig and normal campaigns. A separate opt-in
experimental speed=5 EP0 executor is described below; this is not general USB
3.x device or data-endpoint support.

For speed=1 executable runtime endpoints must be interrupt endpoints, with
packet sizes 1..8 bytes and bInterval 10..255. EP0 is not listed among runtime
endpoints. Full/High Speed runtime validation rules are unchanged. Descriptor
payloads remain fuzzable; topology validation is not USB class conformance.

## UDC diagnostics and normal executor guard

Dual preflight reads maximum_speed and current_speed from the selected UDC,
bounded to 128 bytes per attribute. Missing or unrecognized values stay unknown.
The reported maximum is a ceiling, not proof of every lower-speed mode. The
current value is a port snapshot, not an observation attributed to a campaign.

The normal Device.run rejects a requested speed above a known maximum before
opening its default RawIO and issuing INIT/RUN. It checks mutated children
because each child runs through the executor. Unknown maxima do not block the
normal executor by this guard alone. The requested speed is never rewritten.
The usb_speed_precheck log is not hardware acceptance or proof of link speed.

## Experimental SuperSpeed control-only path

Use only on an isolated stand with a SuperSpeed device-capable UDC and a kernel
Raw Gadget implementation accepting USB_SPEED_SUPER in INIT. Hardware operation
and successful enumeration have not been demonstrated by the mock unit tests.

```sh
python -m usb_device_lab.superspeed_control --config examples/superspeed-control-only.json
```

The example contains dummy controller names. Set udc_driver and udc_device to
the actual Raw Gadget driver identifier and UDC instance before running it.
It sets speed=5 and runtime.experimental_superspeed_control_only=true. The
experimental ControlConfig requires a BOS descriptor selector and forbids every
non-control runtime endpoint. Common storage/protocol validation is reused
through a speed=3 projection; the original speed=5 data is preserved for execution.
Descriptor bytes are not certified for class compliance.

The separate executor sends speed byte 5 to INIT, reuses existing EP0 handling
and WireMutator, and requires a known SuperSpeed-or-higher maximum before opening
RawIO. Before each userspace control response it requires current_speed to report
exactly super-speed. Unknown, downgraded and super-speed-plus values abort without
that response or automatic fallback. Sysfs checks are snapshots, not an atomic
measurement of physical transactions; some UDCs may not expose sufficient data
and will therefore be rejected. UDC/kernel incompatibility can also make INIT
or RUN fail. Logs retain hardware_acceptance=false.

This path does not enable companion-descriptor endpoint activation, bulk or
interrupt data traffic, isochronous streaming, streams, SuperSpeedPlus, USB 3.2
lane modes, or standard campaign/corpus/UI integration. The example is a minimal
control-only vendor-interface baseline, not a complete HID/storage/audio device.
The wire-mutator seed/probability controls mutation of EP0 response bytes.

The bundled normal seed generator and speed-mutation operator still do not
produce Low Speed or experimental SuperSpeed entries automatically. A bcdUSB
value is not proof of link speed; existing attempt cards display the requested
configuration speed. Protocol resources and owned I/O are closed on executor exit.
