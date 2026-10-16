# USB speed modes

The device JSON speed field uses Linux USB speed enum values, not USB version
numbers. The model accepts 1 (Low Speed), 2 (Full Speed, the default), and
3 (High Speed). Values 5 and 6 are still rejected: SuperSpeed/SuperSpeedPlus
runtime support is not implemented by this project.

For speed=1 the executable runtime topology permits interrupt endpoints only,
with packet sizes 1..8 bytes and bInterval 10..255. EP0 remains the control
endpoint; it is not listed among runtime endpoints. Bulk and isochronous
runtime endpoints cannot be used at Low Speed. The existing Full/High Speed
validation rules are unchanged.

Device.run already passes the configured speed byte to USB_RAW_IOCTL_INIT.
Model acceptance does not prove that the selected physical UDC can operate at
that speed. No controller is opened or tested by the new unit tests. Controller
and kernel support must be verified on the stand; this commit does not add
automatic speed negotiation, fallback, or a capability probe.

For an ordinary low-speed device, provide consistent device/configuration/class
descriptors, including an EP0 maximum packet size of 8 bytes. Descriptor payloads
remain fuzzable bytes: validation of the executable endpoint topology is not
USB class conformance validation. The test fixtures are model tests, not a
complete HID implementation or a hardware acceptance suite.

The bundled seed generator and speed-mutation operator are not changed by this
commit. Existing corpus entries are not automatically converted to Low Speed.
A bcdUSB descriptor value is not evidence of the negotiated link speed; the
operator panel displays the requested speed from the saved configuration.
