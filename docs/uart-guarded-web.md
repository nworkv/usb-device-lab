# Preflight-protected UART web launcher

Recommended protected entry point:

```sh
python3 -m usb_device_lab.uart_guarded_web --config examples/lab.uart.example.toml --seeds corpus/seeds/hid-keyboard.json --port 8766
python3 -m unittest discover -s tests -p 'test_uart_guarded_web.py' -v
```

Reuses the existing UART page, HTTP server and supervisor through a small adapter;
no rewrite of uart_web.py or campaign/checkpoint code. The OLD uart_web launcher
remains available and does NOT gain a preflight gate. Direct Python API users
must explicitly use GuardedSupervisor to obtain this behavior. Do not run both
launchers on the same port. Existing SSH workspace is unchanged.

For every new/resumed campaign, guarded start checks busy/closing state, runs the
read-only metadata checker, stores its report, and requires exactly boolean true
in metadata_checks_passed before forwarding the command to the base supervisor.
Rejected inspection never calls base.start. Checkpoint compatibility and config
reload remain the base/campaign responsibility. Status adds preflight,
preflight_pending and preflight_gate; the minimal page displays them in its JSON
status. No new polished UI card or separate preflight HTTP endpoint is added.

Inspection does not hold the base lock: status/stop remain available while it
runs. A stop during inspection cancels pending launch; duplicate starts are
rejected. A stop racing the actual base.start is propagated again afterwards,
but this is not an atomic guarantee of zero transient hardware activity at that
boundary. Filesystem stalls can still delay an HTTP start response/inspection.

All original loopback/token/Host/Origin/artifact limits apply. Token is printed to
operator terminal or taken from USB_DEVICE_LAB_UART_TOKEN; protect that output.
Logout does not stop a campaign. Standard metadata success is not UART console,
electrical compatibility, host health, USB enumeration or exclusive ownership.
The adapter never changes config, clears cleanup markers or offers a skip flag.

Seven adapter tests passed locally with a test base supervisor and injected
checker, including pending-inspection cancellation, duplicate starts, report
isolation, errors, strict success and resume forwarding. Python syntax checked.
Actual HTTP-over-adapter/campaign integration, browser rendering, full repository
CI and hardware were not run. Prior unguarded panel/integration tests are unchanged.
