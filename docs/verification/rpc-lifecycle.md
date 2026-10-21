# RPC lifecycle verification — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Replaces tests/test_rpc_lifecycle.py (inspected blob a103e2a141d299e825f07c0ce6128720d4dd7510).

Change: register unconditional child cleanup immediately after Popen. Cleanup kills a still-running child, waits, and closes RPC streams even if an assertion fails. Add idle-reader and full-queue shutdown coverage; idle shutdown also checks repeated close and receive after close.

PASS: all 6 lifecycle tests with real subprocesses, using a transcription of rpc.py blob 816d744596f9f40daec6b88779fdfa51955fd01e in an isolated module. Includes the four existing scenarios and two new shutdown scenarios.
Preparation issue: the first harness invocation lacked rpc_source and did not run tests; reconstructed the module and reran successfully.
SKIPPED: full project suite, runtime/web suites and USB/KCOV hardware tests; full checkout/dependency environment was not assembled and hardware was not exercised.
Scope: test reliability and shutdown regression coverage; no production code changes and no assertion that every ResourceWarning is resolved.
