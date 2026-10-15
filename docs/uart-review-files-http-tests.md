# Real evidence files -> HTML HTTP attachment

```sh
python3 -m unittest discover -s tests -p 'test_uart_review_files_http.py' -v
```

Five integration cases use real temporary JSON files and loopback HTTP with the
actual reader/decoder/renderer path (no mocks in the committed test module).
The supervisor is a status/config-only fixture: no campaign, UART, Raw Gadget,
SSH or subprocess is started. Tests check generated escaped HTML, UTF-8/stack
text and context, attachment headers, unchanged files, marker rejection/missing
marker, capture-ID mismatches, symlink input rejection and invalid JSON/auth.
A stored malicious review.html is deliberately present; its unique text must
never appear in the generated response. Source log markup is not active HTML.

The existing test_uart*.py runner includes the file automatically. No workflow
or product code changes. The tests do not imply browser rendering, real kernel
bug reproduction, host readiness or end-to-end capture/campaign correctness.

All five passed locally with real filesystem/socket/HTML parser and production
logic reconstructed from approved source: artifact reader, strict JSON decoding,
renderer and the exercised review HTTP/guard branches. Unused page/routes/launch
plumbing were isolated. This is not a full-checkout run; committed tests import
production modules and CI must verify that exact environment. Hardware, browser
and Actions status were not checked. Earlier mock-based HTTP contract tests are
retained; these add coverage of the file-to-renderer boundary they did not test.
