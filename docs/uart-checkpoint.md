# UART checkpoint and resume: stage 6

Campaigns now save checkpoint.json before opening UART and after each completed,
noncancelled attempt. It records next_iteration and the structural Mutator PRNG.
Interrupted/failed attempts do not advance the checkpoint and are repeated on
resume. If power fails between result persistence and checkpoint replacement,
already-executed USB activity can repeat: this is NOT exactly-once execution.

```sh
python3 -m usb_device_lab.uart_campaign --config examples/lab.uart.example.toml --seeds corpus/seeds/hid-keyboard.json --resume var/results-uart/<old-UUID>/checkpoint.json
python3 -m unittest discover -s tests -p 'test_uart_checkpoint.py' -v
```

Same ordered seed contents, UDC, UART settings, timings, iteration target, seed,
strategy/mode/feedback, checkpoint version and Python major/minor are required.
Output locations may change. SSH/KCOV snapshots and already-completed campaigns
are rejected before hardware access. No automatic artifact-recovery scanning or
migration is implemented. Seed paths are not identities; effective seed digests
include the TOML UDC overrides. Future mutator behavior changes must bump VERSION.

Each resume creates a NEW campaign/capture UUID and records resumed_from and
first_iteration. Old files/checkpoint stay unchanged. Monotonic windows from
separate capture sessions are never joined. Resume restores only generation
progress, not host health, UART continuity, USB state or coverage. Check the stand
before restarting, particularly after panic, power loss or incomplete cleanup.
Existing UDC cleanup markers still block execution.

JSON only, bounded 64 KiB regular files, no symlink inputs, strict PRNG validation;
no pickle/eval. Checkpoint writes use a new 0600 temporary file, fsync, replace and
directory fsync. Attempt directories are synced before progress advances. Storage
failures abort the campaign; power-loss durability is not an atomic transaction
with hardware or all artifacts. Inputs/output parent directories must be trusted.
Compatibility fingerprints are not signatures/authentication.

Seven tests passed in an isolated package with reconstructed structural Mutator,
a lightweight DeviceConfig test double, injected UART receivers, the actual
checkpoint/campaign modules and real child processes. Full repository CI, prior
campaign regression suite and physical UART/USB were not run. Dashboard and
checkpoint-based error-verdict/corpus policy are not part of this commit.
