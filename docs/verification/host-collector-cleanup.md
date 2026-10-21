# Host collector cleanup verification — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Source inspected: host_agent.py blob b0e9cf3ec756e9b57b01f022400d37bfd0ad70be; rpc.py blob 816d744596f9f40daec6b88779fdfa51955fd01e.

Fix: collector pipes use binary unbuffered mode and explicit stdout ownership required by Lines; finalize RPC on normal stop, exceptions and input EOF. Report stream cleanup errors on the error response.

PASS: 3 isolated integration tests using the proposed host_agent module and a transcription of current rpc.py, real Python collector subprocesses and pipes; KernelLogs replaced with a fake. Tests cover two start/stop cycles, malformed collector reply with preserved evidence, and EOF with an active child. Each child was reaped and stdin/stdout closed.
Initial test run: two failures due to escaping in the collector test fixture; changed its source to a raw string and reran: 3/3 passed.

SKIPPED: full repository suite and existing runtime/web suites; complete checkout and dependency environment were not assembled in this session.
SKIPPED: real KCOV collector, /dev/kmsg and USB hardware; these were not exercised by the isolated tests.
No claim that all project ResourceWarning messages have been eliminated.
