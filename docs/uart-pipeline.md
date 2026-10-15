# Consolidated UART pipeline: complete first block

```sh
python3 -m usb_device_lab.uart_pipeline --config examples/lab.uart.example.toml
python3 -m usb_device_lab.uart_pipeline --config examples/lab.uart.example.toml --resume var/results-uart/<UUID>/checkpoint.json
python3 -m unittest discover -s tests -p 'test_uart_pipeline.py' -v
```

Uses existing format-1 manifest/family/profile filenames, AND filters and manifest
order. Unknown/empty/missing/escaping selections fail. Metadata preflight precedes
content-verified immutable seed snapshots with UDC overrides. Existing generation,
PRNG/checkpoint compatibility and new-capture resume use the original campaign
engine. Equivalent ordered effective seeds are compatible even if selector labels
change. Candidate archives never feed generation or fabricate KCOV coverage.

After capture stops, raw/index files are scanned incrementally: <=2 MiB batches,
<=4096 chunk records, <=256 KiB full-chunk overlap. Context beyond overlap can be
incomplete; all candidates carry boundary uncertainty. Epochs reset assembly,
transport events are separate diagnostics. Nonoverlapping campaign windows only.
Dense-report limits can fail parsing; no silent success. Partial final JSONL is
excluded; raw tail is counted. No unlimited semantic parser or atomic snapshot.

Each batch emits schema-1 evidence-UUID usable by existing review. Overall
pipeline-UUID stores schema-2 summary, SQLite dedup/diagnostics, reports.jsonl and
exports.jsonl. It is not itself a legacy HTML export. Batch scope/base offsets are
explicit; overlap may repeat viewer candidates. The overall ledger keeps longest
body per epoch/header offset. Associated configs go to corpus_out/uart-candidates,
not learned/confirmed corpus. Raw and original results stay unchanged.

Analysis failures write analysis-error.json and no overall complete marker;
partial completed batch exports can remain. Early errors before capture have no
analysis. Trusted files/directories, disk monitoring and operator stand acceptance
are required; no secret redaction, rotation or multi-file atomic transaction.
Legacy CLI/web are unchanged; this is the canonical CLI for block one. Panel
switching belongs to the next combined UI block, not this commit.

Eight isolated tests passed with lightweight DeviceConfig, reconstructed used
analyzer branches, actual files/SQLite and mocked core resume forwarding. One raw
fixture exceeds 16 MiB. Full checkout CI, real PRNG/checkpoint+PTY wrapper lifecycle,
browser and physical hardware were NOT run. No full-stack readiness claim.
