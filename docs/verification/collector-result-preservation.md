# Collector result preservation — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Baseline host_agent.py blob: 2303ab58f377203a120d24390c6e072d15519410.

Change: snapshot the received stop reply before closing stdin or waiting for the collector. On subsequent failure preserve it under collector_result as diagnostic evidence. Keep top-level coverage_valid=false and pcs=[] on failure. Clear the snapshot on start and after a successfully emitted result so later errors do not reuse an old successful reply. The nested reply is not validated coverage.

PASS: five unit tests executed against the proposed host-agent module in an isolated package harness. Covered stream cleanup failure, nonzero collector exit, stdin-close failure, unchanged flat success response, and no stale snapshot on a later protocol error. Popen, RPC and KernelLogs were mocked; no real collector was run.

SKIPPED: full repository regression, existing real-pipe collector tests, actual KCOV, /dev/kmsg and USB hardware. Full checkout/dependency environment was not assembled for this run.
