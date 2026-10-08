# Live campaign events

```sh
python -m usb_device_lab.event_control --config lab.toml --port 8765
```

This opt-in entry point extends the persistent panel. Existing entry points are
unchanged. Enter the local bearer token and enable Live events. The browser polls
once per second and displays at most 100 recent events using textContent.

GET /api/campaign/events?after=0&limit=100 requires the same bearer token and
Host/Origin checks as the control API. Limits are 1..100; cursors are nonnegative
integers below 2**63. Duplicate and unknown query fields are rejected. The reply
contains events, next, oldest, latest, gap and more. Each event has seq, timestamp
and a supervisor status snapshot. Use next as the subsequent cursor. If gap is
true, older events were pruned; refresh status rather than assuming a complete
history. A cursor newer than the database yields 400; restart from after=0 after
an intentional database replacement.

The SQLite journal in results_dir/supervisor-events.sqlite3 retains 500 events,
with payloads capped at 16 KiB, monotonically increasing identifiers and atomic
insert/prune transactions. Journal errors request cooperative stop and block
further starts via the persistent supervisor. Snapshot JSON and journal SQLite
are not one atomic transaction: an unclean exit can leave their latest entries
out of sync. This is bounded operational telemetry, not a forensic audit log,
SSE stream, exact PRNG checkpoint or kernel-log collector. Repeated status
snapshots are possible. Tokens are not written into events.

Tests:

```sh
python -m unittest discover -s tests -p 'test_event_*.py'
```

Ten targeted tests cover SQLite retention, cursors, reopen, payload validation,
close, HTTP authentication, query validation and live script delivery. Preparation
used real SQLite and loopback HTTP with isolated base HTTP helpers and a mock
supervisor. Supervisor integration, browser JavaScript execution, the full
repository suite and physical UDC/SSH/KCOV operation have not been tested here.
