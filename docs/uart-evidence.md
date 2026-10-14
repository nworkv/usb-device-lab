# UART evidence export: stage 5

Stop capture first. Offline command; never starts UART/USB/SSH or changes originals:

```sh
python3 -m usb_device_lab.uart_evidence --campaign var/results-uart/<UUID> --capture-root var/logs-uart/uart
python3 -m unittest discover -s tests -p 'test_uart_evidence.py' -v
```

Capture location is derived from a validated UUID under the explicitly trusted
capture root, not metadata paths. Leaf symlinks and nonregular files are rejected;
parent paths must be trusted. This is not a malicious-filesystem sandbox or a
multi-file snapshot lock. Per-file change checks do not prove global quiescence.

Each export creates evidence-UUID: analysis.json, summary.json and report JSONs.
complete.json is written last and means export completion only, not complete UART
evidence or a finished campaign. Files use 0600, directory 0700; fsync is not an
atomic transaction. Failed exports may leave partial directories without a marker.

Kernel reports are heuristic candidates, never confirmed bugs/causes. UART capture,
executor exits, infrastructure failures and incomplete attempts are separate,
potentially overlapping diagnostics. All results retain causal=false and
completeness=unknown. Silence is not a hang. No corpus promotion/verdict is made.

Unterminated final JSONL records are excluded and counted, even valid JSON without
a newline. Unindexed raw tails are counted, not parsed. Malformed complete records,
duplicate keys, nonfinite constants and identity/index inconsistencies fail export.
Missing window/result files produce incomplete-attempt diagnostics.

Limits: 16 MiB raw, 32 MiB per JSONL file, 64 KiB per record, 100000 records and
1024 attempts/reports. Larger captures fail explicitly; streaming/live integration,
checkpoint and dashboard are not implemented.

Also fixes the extra closing parenthesis blocking uart_reports/campaign import.
Fourteen tests passed in an isolated package with the reconstructed corrected
analyzer and actual filesystem artifacts. Full repository CI/hardware not tested.
