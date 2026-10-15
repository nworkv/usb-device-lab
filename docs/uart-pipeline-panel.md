# Pipeline operator panel: consolidated block two

```sh
python3 -m usb_device_lab.uart_guarded_web --config examples/lab.uart.example.toml --port 8766
python3 -m unittest discover -s tests -p 'test_uart_pipeline_panel.py' -v
```

Same guarded launcher, new complete panel. Remove --seeds: TOML/manifest selects
inputs. Old uart_web/SSH workspace unchanged; GuardedSupervisor API retained.
Config changes require restart. No browser budget/command overrides.

Reuses block-one selection/snapshot/analysis helpers and original campaign engine.
Mandatory preflight; cancellation checked before dispatch, though boundary races
can cause transient activity. Stages separate preparation, USB, stopping, analysis
and failure. UART is live receiver status. Progress is persisted absolute
next_iteration, including resumed prefix. Execution/analysis errors stay separate.
Analysis is automatic only after capture stops. Stop does not interrupt disk
analysis. Close waits 30s then raises; inspect stalled worker/stand.

History sorts directory modification time, not start/host event time. Details:
last 100 attempts, 20 pipeline dirs, 10 reports and 20 diagnostics per ledger.
Report/context UI caps 64/16 Ki characters with truncation flags; originals stay
available. Diagnostics may be clipped. JSON/JSONL/SQLite downloads allowlisted,
regular/no-leaf-symlink, <=32 MiB (413 otherwise). Existing batch HTML download
uses the same token. Dynamic textContent only; memory token, no token URLs/storage.

Loopback/Bearer/Host/Origin restrictions remain. Protect token output and artifact
secrets. Trusted parent paths/SQLite required; no public hardening/redaction or
atomic input snapshot. Candidates are not confirmed bugs/causes/host readiness.

Six isolated tests passed with real file/SQLite reads, reconstructed reader and
mock pipeline/core/loader/receiver; Node JS syntax checked. Full HTTP+core+PTY,
browser, repository regression/CI and hardware NOT run. One UI block, not a
verified stand release. Existing CI discovers the new test_uart* file.
