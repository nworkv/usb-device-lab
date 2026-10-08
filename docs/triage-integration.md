# Kernel triage integration checks

## Scope

The feature/kernel-triage branch adds kernel-evidence classification, per-run
artifacts, iteration summaries and a read-only dashboard. It does not yet add
automatic finding confirmation, minimization, a React frontend, corpus browsing
or campaign control from the browser.

A kernel_candidate is unconfirmed kernel evidence, not proof of a unique bug
or vulnerability. Absence of a kernel report does not prove absence of a bug.
Legacy runs without verdict remain legacy_unclassified in the dashboard.

## Automated acceptance

The Kernel triage integration workflow builds the C collector and runs all
repository unittest tests on Python 3.11 and 3.12. Its status must be checked
for the exact commit being deployed. Committing a workflow is not evidence
that the workflow ran or passed. GitHub Actions must be enabled for the repo.
The existing tests.yml workflow is retained unchanged.

Equivalent local checks, from the repository root:

```bash
python3 -m compileall -q usb_device_lab tests
make all
python3 -m unittest discover -s tests -v
```

For focused tests:

```bash
python3 -m unittest discover -s tests -p 'test_triage_errors.py' -v
python3 -m unittest discover -s tests -p 'test_triage_storage.py' -v
python3 -m unittest discover -s tests -p 'test_runner_summary.py' -v
python3 -m unittest discover -s tests -p 'test_web_triage.py' -v
```

The tests use synthetic data and local HTTP servers. They do not require
root, USB hardware, a working /dev/kmsg or SSH credentials. These checks do
not prove real remote USB KCOV collection, browser JavaScript execution,
finding reproducibility or crash recovery.

## Hardware acceptance (separate)

Stop active campaigns before deploying a new revision. Use matching repository
revisions on the gadget and target host. Preserve lab.toml and existing results.
Confirm UDC selection, the physical USB bus, SSH and constrained sudo permissions.
The hardware check and short campaign run on the gadget:

```bash
sudo python3 -m usb_device_lab.lab check --config lab.toml
sudo python3 -m usb_device_lab.lab fuzz --config lab.toml --iterations 3 --seconds 5 --seed 123
```

Start the campaign only after READY. Confirm that new completed run directories
contain run.json, coverage.json, kernel_events.json, kmsg.delta.log and verdict.json.
Inspect coverage_valid and the namespace. READY alone does not prove nonempty
remote coverage or that all campaign cleanup commands are authorized.

## Artifact interpretation

- input.executed.json is the configuration passed to the executor, not a capture
  of actual USB wire bytes or an exact record of runtime wire mutations.
- new_coverage/no_change describe accepted coverage relative to the stored namespace.
- Kernel evidence takes priority over coverage in the verdict.
- interrupted means a run did not finish; it does not identify the cause.
- Files are atomically replaced individually. Files and SQLite are not a single
  crash-safe transaction, and index reconstruction is not yet implemented.
- Legacy database status ok/error is kept for compatibility; use verdict.outcome
  for the detailed classification.

## Dashboard

Run on the machine holding the results:

```bash
python3 -m usb_device_lab.web --output var/results --port 8080
```

The server binds to 127.0.0.1. Use an SSH local forward for remote viewing.
It remains read-only and must not be presented as a campaign launch service.
Results created by root may require appropriate local read permissions.

## Release gate

Do not merge or deploy solely because files have been committed. Require:

1. Both CI matrix jobs pass for the selected revision.
2. Existing CLI and storage tests pass, not only the new tests.
3. Manual browser verification of runs, details and artifact downloads.
4. A short hardware run with accepted coverage on the intended USB bus.
5. A separately scheduled replay of any kernel_candidate before claiming a
   reproducible finding.
