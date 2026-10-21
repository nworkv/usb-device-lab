# Runtime fixture cleanup verification — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless.
Baseline tests/test_runtime.py blob: 751b03139e70fa06854d26d37a9ea6dd686aaa3e.

Changes: fake agent uses Path.read_text/write_text instead of unclosed open() calls. WebTests.get closes the HTTP response via a context manager and closes the connection in finally, including request, getresponse and read errors. No production code changes.

PASS: syntax compilation of the proposed full test file and embedded fake agent and collector.
PASS: extracted fake agent run in three real Python subprocesses with ResourceWarning enabled. Counter reached 3, expected coverage sequence and second-iteration KASAN evidence were preserved; no ResourceWarning in captured child stderr.
PASS: extracted WebTests.get exercised on success and request/getresponse/read failures with mocked HTTPConnection. Connection close called exactly once in all four cases; response context exited on success and read error.
Preparation: embedded-string escaping errors in the local transcription were caught by syntax checks, corrected and checks rerun successfully.

SKIPPED: importing/running complete test_runtime.py, full repository regression and hardware tests. The complete checkout and dependency environment were not assembled; HTTP cleanup checks used mocks, not the project web server. No claim that all project ResourceWarning sources are fixed.
