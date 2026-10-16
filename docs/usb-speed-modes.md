# USB speed modes

Normal DeviceConfig and campaigns accept Linux speed enum values 1 (Low Speed),
2 (Full Speed, the default), and 3 (High Speed). The normal allowlist is
usb_device_lab.usb_speeds.SUPPORTED_MODEL_SPEEDS. Normal campaigns still reject
5 and 6. Experimental speed=5 EP0 execution is separate, not general USB 3.x
data-endpoint support.

## Normal runtime and mutation engine v3

Low Speed runtime endpoints must be interrupt endpoints with packet sizes
1..8 bytes and bInterval 10..255. EP0 is not a runtime endpoint.

Engine usb-tree-model-valid-v3 can select Low Speed when all executable runtime
endpoints are interrupt endpoints (including control-only configurations). It
does not convert bulk endpoints into interrupt endpoints. A transition to Low
Speed caps runtime packet sizes at 8 and raises bInterval to at least 10.
Dependent descriptor changes are included in the mutation trace. Low Speed
runtime endpoint edits retain interrupt type and Low Speed packet/interval bounds.
Full/High Speed bulk packet-size adjustment remains in place.

Advertised descriptor bytes are not automatically made class-conformant or
consistent with the executable topology. They remain fuzzable bytes. Neither
model validation nor speed mutation proves UDC compatibility or enumeration.
The bundled seed generator writes dedicated Low Speed baseline profiles (`hid-low-speed` family: keyboard, mouse, consumer-control, gamepad, multi-report). Those profiles carry `bMaxPacketSize0 = 8`, interrupt-only runtime endpoints with MPS <= 8 and poll interval >= 10 ms, so they pass the topology checks and can be selected for campaigns whose chosen UDC accepts Low Speed device plugs. Mutated descendants of Full Speed interrupt-only seeds can also transition to Low Speed through the mutator's speed field. The executor passes the configured speed to the Raw Gadget INIT ioctl and its precheck rejects requests above the UDC's reported maximum speed ceiling, but UDC speed metadata alone does not prove that enumeration at the requested Low Speed actually succeeded on hardware.
The standard mutator does not generate experimental SuperSpeed configurations.

The mutation PRNG sequence changed. UART/SSH checkpoint version is now
usb-tree-model-valid-v3-round-robin: checkpoints from v2 are rejected rather
than silently continued with a different algorithm. Start a new campaign, or
replay a saved configuration. WireMutator's byte-mutation algorithm is unchanged.

## UDC diagnostics and executor guard

Preflight reads maximum_speed and current_speed from the chosen UDC, bounded
to 128 bytes per attribute. Unknown values stay unknown. A reported maximum
is not proof of every lower-speed mode; current_speed is a port snapshot, not
a campaign-attributed measurement.

Normal Device.run rejects speeds above a known UDC maximum before INIT/RUN,
including mutated children. Missing maxima do not block by this guard alone.
No speed fallback or descriptor rewriting occurs. usb_speed_precheck logs are
not hardware acceptance. Protocol resources and owned I/O are closed on exit.

## Experimental SuperSpeed EP0 executor

```sh
python -m usb_device_lab.superspeed_control --config examples/superspeed-control-only.json
```

Use an isolated stand. Replace the example's dummy controller bindings with
the actual Raw Gadget driver identifier and UDC instance. The kernel must accept
USB_SPEED_SUPER in INIT. Hardware operation and enumeration remain unverified.

ControlConfig requires speed=5, the explicit boolean
runtime.experimental_superspeed_control_only=true, a BOS descriptor selector,
and no runtime data endpoints. It reuses common validation through a speed=3
projection while preserving the original speed=5 execution data.

The separate executor sends 5 to INIT and reuses EP0 handling and WireMutator.
It requires a known SuperSpeed-or-higher maximum before opening RawIO, then
requires current_speed to report exactly super-speed before each userspace
control response. Unknown, downgraded, and super-speed-plus values abort without
that response. Sysfs checks are snapshots, not atomic physical measurements.
Kernel/UDC incompatibility can still make INIT/RUN fail. Logs keep
hardware_acceptance=false; there is no automatic fallback.

This path does not implement companion-descriptor endpoint activation, bulk or
interrupt data traffic, isochronous streaming, streams, SuperSpeedPlus, USB 3.2
lane modes, or integration with standard campaigns/corpus/UI. The example is
a control-only vendor-interface baseline, not a complete device class.
A bcdUSB value is not proof of link speed; attempt cards show requested speed.
