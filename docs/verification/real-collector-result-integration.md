# Real collector result integration — 2026-10-21

Target: nworkv/usb-device-lab, feature/uart-agentless, baseline 885eed6351a32c8054c5dd4fd5cb29e76f031afc.

PASS: 3 isolated integration tests with real Python collector subprocesses and OS pipes. Exercised real Lines/send/receive/close and host-agent logic. Tested preserved stop reply and stderr on injected post-close cleanup error, preserved reply on collector exit 7, and unchanged successful stop response. Asserted child reaping, stdin/stdout closure and reader-thread termination. KernelLogs remained a fake; the cleanup-error test deliberately injects an error after real stream closure.

Executed module copies were transcribed from connector/source context and checked against Git blob hashes: rpc.py f35a203a42a7de97c229bbc7c61090a5a16b8a21; host_agent.py 7f447c0068d1358a6cf677340c14ff3b4d4c9dcc. Both matched. This was not a complete repository checkout.
Preparation: embedded collector newline escaping was corrected and compiled before running tests.

SKIPPED: full regression suite, actual KernelLogs, KCOV and USB hardware. Full checkout/dependency environment was not assembled; no hardware exercised.
Scope: test-only commit; no production changes.
