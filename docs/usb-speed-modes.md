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
null. These diagnostics do not open Raw Gadget, connect to SSH, bind a gadget,
change speed, or provide automatic fallback. They do not add a compatibility
gate for individual seeds or mutated children. The existing metadata checks
remain separate from hardware acceptance.

Device.run passes the configured speed byte to USB_RAW_IOCTL_INIT. Model
acceptance does not prove that the selected physical UDC can operate at that
speed. Controller/kernel support must still be verified on the stand.

For an ordinary low-speed device, provide consistent device/configuration/class
descriptors, including an EP0 maximum packet size of 8 bytes. Descriptor payloads
remain fuzzable bytes: runtime topology validation is not USB class conformance
validation. The bundled seed generator and speed-mutation operator do not yet
produce Low Speed entries automatically. A bcdUSB descriptor value is not proof
of negotiated link speed; attempt cards show the requested configuration speed.
