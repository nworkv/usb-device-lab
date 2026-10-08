# Local campaign control panel

This panel is separate from the existing results viewer. It uses CampaignSupervisor
and does not change the existing lab CLI or web viewer.

## Start

```sh
python -m usb_device_lab.control_http --config lab.toml --port 8765
```

Open http://127.0.0.1:8765 on the gadget machine. The token is printed to the local
terminal; enter it in the password field. It is not embedded in HTML or persisted
in browser storage. Treat the terminal output as sensitive. For a fixed token,
set USB_DEVICE_LAB_CONTROL_TOKEN to a strong random ASCII value of at least 32
characters. Token length validation does not guarantee entropy.

Only 127.0.0.1 is bound; there is no public-bind option. For a remote browser use an
SSH tunnel forwarding local port 8765 to gadget port 8765, preserving the browser
URL http://127.0.0.1:8765. This is an operator tool, not a multi-user service.

## API

Every API request requires Authorization: Bearer <token>.

- GET /api/campaign/status: supervisor state; 200.
- POST /api/campaign/start: JSON object with optional iterations, seconds, seed,
  families, profiles; 202 means the asynchronous request was accepted, not that
  hardware checks have completed successfully.
- POST /api/campaign/stop: JSON {}; finishes the current attempt before stopping.
- POST /api/campaign/resume: JSON {}; starts a new session using previous parameters
  and existing corpus, not a PRNG checkpoint.

All POST requests require Content-Type: application/json and one Content-Length.
Bodies above 16384 bytes are rejected. Unknown fields, duplicate JSON keys and
non-finite JSON constants are rejected. Transfer-Encoding is unsupported.
Host must match 127.0.0.1:<port>; if Origin is present it must match that HTTP origin.
No CORS access is provided. The HTML shell is public but reveals no campaign state.
The panel uses textContent for results, no-store, nosniff and a restrictive CSP.

Errors: 400 invalid request, 401 authentication, 403 Host/Origin, 404 route,
409 supervisor conflict, 413 size, 415 media type, 500 internal error,
503 shutdown. SIGINT/SIGTERM requests cooperative stop and waits for cleanup;
this is not an emergency hardware-reset mechanism.

## Tests and scope

```sh
python -m unittest discover -s tests -p 'test_control_http.py'
```

Ten HTTP tests cover authentication, host/origin checks, forwarding, validation,
size limits, error mapping, token validation and shutdown rejection. Tests use a
real loopback HTTP server with a mock supervisor; no physical UDC, SSH host or
KCOV collector is exercised. The full repository suite has not been run here.
Supervisor state persistence, checkpoint continuation and event streaming are
outside this commit. The panel deliberately exposes no arbitrary command API.
