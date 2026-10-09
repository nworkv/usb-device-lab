# UART analysis: stage 3

Pure offline API; no hardware access, external dependencies, CLI or campaign integration.

```python
from usb_device_lab.uart_reports import AttemptWindow, analyze
w = AttemptWindow('attempt-1', 'capture-id', 100, 400, 200, 300)
result = analyze(raw_bytes, chunk_records, [w], event_records, capture_id='capture-id')
```

Times are Pi monotonic nanoseconds. Capture windows are half-open; active_start_ns/active_end_ns delimit the active phase. The caller supplies identity and correct clock domain. This module does not persist attempt boundaries or prove clock identity.

fragments provide original byte ranges and connection epochs. Whole chunks are selected by receive time, not per-byte or host timestamps. reports provide header/body offsets, preceding context, termination reasons and candidate associations with pre/active/post receive phases. Multiple matching windows are ambiguous. Post-phase reception is not proof of late generation. Associations are never causal; completeness is always unknown. Silence is not a hang.

Header detection is heuristic for BUG, WARNING, Oops, panic and sanitizer prefixes. UTF-8 backslash replacement only affects display text; raw data stays unchanged. End-trace markers are parser boundaries, not proof of report completeness. Limits, gaps, next headers and capture end mark possible truncation. Gaps and epoch changes reset context and assembly.

The index must describe a contiguous prefix from offset zero and sequence one, with nondecreasing receive times and epochs. Explicit gap offsets must be chunk boundaries. Unindexed raw tails are counted but not parsed. The caller handles file loading and partial JSONL records; inputs here are decoded records.

Limits per call: 16 MiB raw, 100000 chunks/events, 1024 attempts/reports, context up to 64 lines and bodies up to 4096 lines. Invalid metadata and exceeded limits raise ValueError. Unlimited/streaming captures need a later reader/index integration; this is not a finished campaign analyzer.

Run: python3 -m unittest discover -s tests -p 'test_uart_reports.py' -v
Fourteen targeted tests passed in an isolated package using the actual analyzer. Full repository CI, real kernel reports and hardware were not tested. Receiver and SSH/KCOV code remain unchanged.
