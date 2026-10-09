# UART campaign: stage 4

Linux / Python 3.11+. Explicit preselected seeds, no SSH/agent/KCOV:

```sh
python3 -m usb_device_lab.uart_campaign --config examples/lab.uart.example.toml --seeds corpus/seeds/hid-keyboard.json
python3 -m unittest discover -s tests -p 'test_uart_campaign.py' -v
```

Run only on a dedicated authorized stand. Configure the real UART format and UDC
before running. The CLI opens hardware and starts the local Raw Gadget executor;
it is NOT a dry run. The default UDC lock directory is /run/lock/usb-device-lab
and must be safely owned by the executing user (normally the stand administrator).
The implementation uses the existing DeviceConfig/Mutator and device module.

--seeds explicitly overrides corpus/manifest/profile/family selection; this stage
does not apply those selectors or use gadget.device_config as an implicit seed.
Seeds rotate round-robin, run unchanged once each, then receive seeded mutations.
UDC identifiers from TOML override each seed. Up to 10000 attempts are supported.
No coverage-guided corpus growth or fake PCs are introduced.

One UARTReceiver runs throughout the campaign. The executor starts only after the
local tty has opened; connected is not proof that the host console is working.
Startup is limited to 10 seconds. Between attempts/pre-launch, a disconnected tty
halts the campaign. During a window the receiver can reconnect; completeness
remains unknown. Fatal receiver failure stops the executor and aborts the campaign.

Each campaign uses a UUID shared by its result and capture directories and saves
campaign.json (including Pi boot ID where available). Each attempt saves config,
started metadata, executor.log, window.json and result.json. Window times are Pi
monotonic nanoseconds. Active boundaries describe launch/stop intent, not confirmed
USB enumeration or the host's event times. Post-capture starts after executor
cleanup. Ctrl+C/SIGTERM request cancellation; canceled windows are saved.

Executor shutdown targets its new-session process group: SIGTERM then SIGKILL,
with waits of eight seconds each. A failed reap aborts the campaign and writes an
unreaped-PID marker into the UDC lock file. Further campaigns using that lock root
refuse to start. Inspect the process and physical stand before manually clearing
that marker; never blindly delete it. This is cooperative locking, not protection
against other USB software or a different lock root. SIGKILL cannot guarantee a
kernel-blocked process will exit. Filesystem failures can prevent evidence/marker
persistence; recovery in that case is an operator responsibility.

Artifacts are exclusive-create, fsynced files but not an atomic campaign database.
Hard termination can leave only started.json; checkpoint/recovery is a later stage.
Logs and artifacts are not rotated. UART thread shutdown is checked; blocking
filesystem/driver calls can prevent timely stop. Do not assume failure means all
hardware is released.

This commit does not load/parse capture JSONL, invoke uart_reports automatically,
classify kernel failures, implement manifest preparation, checkpoint or dashboard.
Results use coverage_valid=false, pcs=[], causal=false and completeness=unknown.
window_completed means only that the executor process remained alive for the local
time window, not that enumeration or USB activity succeeded.

Fourteen targeted tests passed in an isolated package with reconstructed repository
DeviceConfig/topology/Mutator code, real local child processes and injected test
UART receivers. These are orchestration tests, not full-repository CI or physical
UART/USB integration tests. Existing receiver, report analyzer and SSH code are unchanged.
