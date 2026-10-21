# Host-agent EOF cleanup — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Baseline host_agent.py inspected in the preceding review: blob 9f4d8641e70acfcee066f493b3eadc6545bbaea1.

Change: consolidate final cleanup and emit error responses after cleanup completes. On EOF, collector kill/wait/stream cleanup errors now produce status 1 and collector_cleanup_error. Preserve the original protocol error alongside cleanup diagnostics and available kernel evidence. Clean EOF retains the existing status 0 behavior.

PASS: proposed source and test module compiled and executed in an isolated package harness. Six unit tests passed: clean EOF; kill failure; wait timeout; stream cleanup failure; original error plus cleanup failure; kernel evidence after log close failure. Popen, Lines, close_rpc_streams and KernelLogs were mocked. No real subprocess/USB behavior was validated by these six tests.

SKIPPED: full repository suite and existing real collector lifecycle tests; full checkout/dependency environment not assembled for this run.
SKIPPED: real KCOV, /dev/kmsg and USB hardware.
Remaining scope: preserving a final collector stop response when cleanup fails is not implemented by this commit.
