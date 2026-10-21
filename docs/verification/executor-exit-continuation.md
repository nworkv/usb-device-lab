# Executor early-exit continuation fix — 2026-10-21

Changes: dual_campaign saves a reaped executor early exit as executor_error, retains config/window/result/log and advances to the next attempt if capture remains connected. Both UART/SSH and panel/CLI use this engine. Nonzero exit status is still indexed by the existing event registry. Early status 0 is saved in result.json but separate card indexing is not added here.

Also restore the batch group/world-write guard (reverse-change hash matched prior b12b6182b1474d4d740d2af0b5ef73689b1c5c91), and repair runner test handshake plus fake termination so no fixture PID receives a signal.

PASS: 3 engine test methods with real children (6 UART/SSH status 0/1/2 subcases, Stop, command-factory failure), 1 permissions test with 2 modes, and 1 runner cleanup test. Engine model/receiver/checkpoint/save/terminate were fixtures in the local harness. Runner body was reconstructed, not imported from a full checkout. A missing local harness dependency initially prevented permissions checks; corrected and rerun successfully.
SKIPPED: full cleanup gate, full repository suite, original EngineTests environment, event-registry/UI tests, physical UART/SSH and USB. No claim all failures or hardware hangs are recoverable. Capture loss, failed termination, command construction and persistence errors retain fatal behavior; Stop remains effective.

After approval: stop old panel/campaign, preserve local edits, update checkout with git pull --ff-only, then rerun cleanup gate and test_executor_continue.py. Keep iterations within 1..10000. Run dual_cli check then run or serve. Do not delete logs or cleanup markers. An already stopped campaign will not restart automatically.
