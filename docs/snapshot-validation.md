# Strict supervisor snapshot validation

Persistent and event supervisors now use the same version-1 validator before
writing a snapshot and before applying a restored snapshot. The format version
and existing panel entry points are unchanged. Validation never starts a campaign.

Rejected input includes duplicate JSON keys (also nested), NaN/Infinity constants,
invalid UTF-8, excessive nesting, more than 65536 bytes, unknown or missing schema
fields, boolean integers, invalid states, negative or overflowing run counts,
non-string identifiers/errors and invalid saved campaign parameters.

Saved limits match campaign numeric limits: iterations 1..1000000, seconds >0
and <=300, seed 0..2**63-1. Selectors must be JSON lists with at most 128 strings,
each nonempty and at most 127 characters. Errors are limited to 4096 characters;
identifiers to 128. Selectors become tuples after decoding. Manifest membership
and seed-file availability are still checked when explicitly starting/resuming.
These selector limits are additional snapshot constraints; custom oversized
snapshots accepted by older code may now fail validation.

An invalid restore raises ValueError before applying status or saved parameters.
The snapshot is not silently deleted, repaired or migrated. Inspect the file and
lab before manually restoring a known-good backup. Recovered active states still
become interrupted without automatic restart. An invalid save leaves the previous
file intact, reports persistence_error and requests cooperative stop through the
existing persistence failure path.

```sh
python -m unittest discover -s tests -p 'test_snapshot_schema.py'
```

Ten targeted tests passed during preparation using the real decoder and real
_save/_restore implementations in an isolated package. The base supervisor was
stubbed; the full repository suite, lifecycle integration, browser, SSH, KCOV and
physical UDC were not exercised. The old persistent_control.py content was checked
against its current Git blob SHA before generating the change.
