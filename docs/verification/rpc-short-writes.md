# RPC short-write verification — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Baseline rpc.py from preceding review: blob 816d744596f9f40daec6b88779fdfa51955fd01e.

Change: Lines.send advances through a memoryview until the complete encoded line is written. Retry InterruptedError without advancing; fail on zero/None progress or invalid counts; propagate pipe/flush failures and check reader closure between writes. The existing blocking write API is retained; this is not a send-deadline or concurrent-send implementation.

PASS: proposed rpc.py and test module compiled and ran in an isolated package harness. Eight unit tests passed with mocked binary stdin: short JSON writes, interrupted write, no progress, invalid count, broken pipe, closed reader, closure during partial write and flush failure. No reader thread was started by these unit tests.

SKIPPED: full repository suite, real-pipe lifecycle/host-agent integration and hardware tests. Full checkout/dependency environment was not assembled for this run; these checks are not claimed as passed.
