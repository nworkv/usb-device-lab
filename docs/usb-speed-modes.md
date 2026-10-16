# USB speed modes

The device JSON speed field uses Linux USB speed enum values, not USB version
numbers. The model accepts 1 (Low Speed), 2 (Full Speed, the default), and
3 (High Speed). Values 5 and 6 are still rejected: SuperSpeed/SuperSpeedPlus
runtime support is not implemented by this project. The single source for
accepted model speeds is usb_device_lab.usb_speeds.SUPPORTED_MODEL_SPEEDS.

For speed=1 the executable runtime topology permits interrupt endpoints only,
with packet sizes 1..8 bytes and bInterval 10..255. EP0 remains the control
endpoint; it is not listed among runtime endpoints. Bulk and isochronous
runtime endpoints cannot be used at Low Speed. Full/High Speed validation
rules are unchanged.

## Read-only UDC diagnostics

The dual preflight report includes usb_speeds. It reads maximum_speed and
current_speed from the selected /sys/class/udc entry, bounded to 128 bytes
per attribute. Missing, unreadable, oversized, unknown, and unrecognized
values stay unknown; they are not silently converted to Full Speed.

maximum is the controller's reported ceiling, not a list of every supported
mode. It does not prove support for Low Speed. current_at_inspection is a
snapshot of the port's negotiated speed when inspected; it is not attributed
to a particular attempt or to this process. controller_supported_modes remains
null. Preflight diagnostics do not open Raw Gadget, connect to SSH, bind a
gadget, change speed, or provide automatic fallback. Metadata checks remain
separate from hardware acceptance.

## Executor speed-ceiling guard

Before opening its default RawIO and issuing INIT/RUN, Device.run checks the
requested configuration speed against the selected UDC's reported maximum.
A request above a known maximum is rejected with no fallback. This also checks
mutated children because it runs inside each executor process. Unsupported
software speeds and invalid UDC path components are rejected before the check
reads sysfs. The code does not change the requested speed or rewrite descriptors.

When maximum_speed is unavailable or unrecognized, the comparison remains
unknown and execution is not blocked by this guard alone. Being below the
reported maximum is not proof that the controller supports that lower speed.
The usb_speed_precheck executor log records the requested speed and the ceiling
comparison, not a measured or confirmed per-attempt link speed. Controller and
kernel compatibility still require stand verification. Protocol resources and
already-owned I/O are closed if the guard rejects the configuration.

For an ordinary low-speed device, provide consistent device/configuration/class
descriptors, including an EP0 maximum packet size of 8 bytes. Descriptor payloads
remain fuzzable bytes: runtime topology validation is not USB class conformance
validation. The bundled seed generator and speed-mutation operator do not yet
produce Low Speed entries automatically. A bcdUSB descriptor value is not proof
of negotiated link speed; attempt cards show the requested configuration speed.
