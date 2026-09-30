# Architecture

## Separation of data and execution

`advertised descriptors` are opaque bytes returned to a host; they may deliberately be malformed. `runtime topology` is independently validated against Raw Gadget/UDC constraints so the executor can remain alive long enough to fuzz the host. A protocol handler determines control, IN and OUT behavior after enumeration.

## Coverage decision

The host agent starts remote KCOV before USB attachment, reads coverage after disconnect, labels PC addresses with kernel release/boot ID/bus namespace, and records kernel logs. A case enters corpus only when coverage is valid, non-saturated and contains a new PC in its namespace. Errors are persisted regardless of corpus admission.

## Mutations

Structural mutation changes descriptor/control/script data in JSON. Wire mutation deterministically changes emitted control/IN responses after protocol handling. Both seeds and operations must be saved with a run.

## Safety boundaries

Plugin handlers are trusted local code. A proxy to a real reference device may forward destructive writes and must select exactly one device. The web server must stay loopback/read-only.
