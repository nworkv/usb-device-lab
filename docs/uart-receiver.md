# UART receiver: stage 2

Linux / Python 3.11+. No additional Python dependencies. Uses UARTSettings from stage 1.

```sh
python3 -m usb_device_lab.uart_receiver --config examples/lab.uart.example.toml
python3 -m unittest discover -s tests -p 'test_uart_receiver.py' -v
```

Replace the example port and framing with your actual adapter and host-console settings. Use a new --output directory to select an explicit capture location; the default is logs_dir/uart/<UUID>. Existing capture directories are rejected. Ctrl+C and SIGTERM request orderly shutdown.

Files: uart.raw contains exactly the bytes returned by the tty driver; chunks.jsonl maps each received chunk to byte offsets [offset_start, offset_end), wall-clock and monotonic nanoseconds, and a connection epoch. events.jsonl records startup, connections, failures, disconnects and shutdown. Byte offsets remain continuous across reconnections; an epoch boundary and disconnect event indicate potentially missing data, not a continuous source stream.

The port is opened read-only, with raw terminal processing and no software/hardware flow control. No UART data writes, SSH, subprocesses or host agents are used. No input flushing is performed. Settings are restored on close when the device still responds. Opening/configuring a tty can affect modem-control lines through its driver; this is not a guarantee of electrically passive DTR/RTS behavior.

flock and TIOCEXCL prevent competing instances and ordinary new opens. Already-open readers and privileged programs can bypass these protections: stop other software using the adapter. connected means the local tty was opened/configured, not that the host is healthy. Silence is not classified as a hang. completeness is always unknown, including captures with no observed disconnects.

fsync is periodic (one second, checked between reads), and forced on disconnect and shutdown. Hard termination or power loss can leave a raw tail without a matching complete index record; durability is not an atomic transaction across files. Storage errors stop capture rather than masquerading as UART reconnects. Logs are unbounded: monitor disk space. The synchronous stop request is observed between reads/retry waits; filesystem calls can delay shutdown.

This stage does not integrate the receiver with lab.load, dashboard or campaigns; it does not parse reports or bind logs to fuzzing attempts. Tests exercise real Linux PTYs and simulated storage failure. Twelve tests passed in an isolated package using the stage-1 UARTSettings definition; complete repository CI and physical UART hardware were not tested.
