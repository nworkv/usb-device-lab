# Guarded UART HTTP checks

```sh
python3 -m unittest discover -s tests -p 'test_uart_guarded_http.py' -v
```

Five tests connect the existing UART HTTP Server/Handler to GuardedSupervisor
using real loopback TCP connections. The campaign-facing base supervisor and
metadata checker are explicit test doubles; no UART, USB, SSH or child campaign
is started. The tests check:

- failed metadata returns 409 and remains visible through authenticated status;
- missing token/wrong Origin never reach preflight or campaign start;
- successful new/resume requests each run the checker and forward the UUID;
- inspection exceptions produce a visible failed report without launch;
- while a start request waits for preflight, status remains accessible, another
  start is rejected, and stop cancels the pending launch (first request ends 409).

Included automatically by the existing test_uart*.py CI runner. No workflow,
product code or old launcher behavior changes in this commit. The old unguarded
uart_web command is still unguarded; use uart_guarded_web for the preflight adapter.

All five passed locally on real sockets in an isolated package using Server,
Handler and adapter definitions reconstructed from the approved source. Unused
page/assets/artifact routes were isolated and not exercised. The committed tests
import production modules on a full checkout; repository CI must confirm that
run. Browser behavior, full campaign/checkpoint/PTY chain and physical hardware
are not verified by these tests. Passing HTTP/preflight is not host readiness.
