# Separate UART web page: stage 7

```sh
python3 -m usb_device_lab.uart_web --config examples/lab.uart.example.toml --seeds corpus/seeds/hid-keyboard.json --port 8766
python3 -m unittest discover -s tests -p 'test_uart_web.py' -v
```

NEW separate development page, not a tab in the existing workspace. Old dashboard,
SSH supervisors and corpus UI stay unchanged. Explicit seeds; no runtime budget
or manifest overrides. Config edits require restarting this web process.

Binds only to 127.0.0.1. If administratively forwarding access, preserve the same
localhost port. Do not publicly expose this development HTTP server. Random token
is printed to the operator terminal, or set USB_DEVICE_LAB_UART_TOKEN (32..128
ASCII URL-safe characters). Protect terminal/service logs. API uses Authorization,
Host/Origin checks, no cookies/CORS/token URLs. Browser token stays in memory;
logout aborts requests and clears data, NOT the running campaign.

Authenticated start/stop/resume uses a background UART campaign worker, fixed
config and seeds. Resume takes a known UUID, not a client path or command. Async
failures appear in status. 'running' is worker activity, not enumeration/host
health. Shutdown requests cancellation and waits 30 seconds; inspect the stand
if cleanup fails. Kernel-blocked children/filesystem stalls remain limitations.

History is bounded to 1000 UUID directories, sorted by UUID, not time. Downloads
cover allowlisted campaign/checkpoint, attempt, UART and evidence files: regular
files, no leaf symlinks, at most 32 MiB (413 otherwise). Trusted parent paths are
required; files can change while read. No secret redaction is implemented.
Evidence is generated separately by the offline exporter and may be partial;
complete.json means export completion only. Summary/analysis are raw JSON views,
not polished report cards. Interface has Russian controls and original JSON keys.

Twelve tests passed with actual loopback HTTP/filesystem and injected supervisor,
config-loader/campaign-runner dependencies. Python and Node.js JS syntax checks
passed. Browser rendering, full campaign integration, existing workspace
regressions, repository CI and hardware were not tested.
