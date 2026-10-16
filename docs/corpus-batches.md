# Sequential corpus batches

This CLI executes existing seeds once each, family by family. Each family is a
separate ordinary UART/SSH campaign with at most 128 parents. The batch limit
is 4096 planned attempts. It does not generate variants or run different groups
in parallel. For a generated corpus, each family represents one source profile:
22 groups of 50 variants therefore schedule 1100 attempts.

## Plan first

```sh
python -m usb_device_lab.batch_campaign --config "$HOME/.config/usb-device-lab/lab.dual.toml" --all-groups
```

Without --execute this only reads configuration/seeds and prints a plan. It does
not create batch output, connect telemetry, open Raw Gadget or launch USB.
By default configured family/profile filters apply. --all-groups explicitly
clears those filters. CLI reads TOML, not the panel's in-memory corpus activation;
point TOML at the generated manifest/seeds first if that is the intended input.
--mode uart/ssh overrides telemetry.mode, provided that transport is configured.

## Execute on an isolated stand

```sh
python -m usb_device_lab.batch_campaign --config "$HOME/.config/usb-device-lab/lab.dual.toml" --all-groups --execute
```

The group iteration budget is its selected seed count, not campaign.iterations.
Timing, quiet/capture windows, UDC binding and transport use the loaded config.
The engine's first pass uses each parent without additional structured mutation;
existing per-seed WireMutator settings still apply. Producing 50 variants is a
separate corpus-generation operation, not something this runner invents.

Selected paths/digests are frozen in plan.json. dual_pipeline checks the expected
selection before preflight/preparation, and existing preparation verifies each
seed digest again. A changed selection is rejected rather than silently run.

Output lives under results_dir/batches/<id>/ with atomic plan.json/progress.json
snapshots. Ordinary per-group campaign/capture artifacts stay in their existing
locations and are linked by campaign_id. attempts_recorded includes intentionally
skipped attempts; skipped_attempts is reported separately. Neither count proves
USB enumeration or completed physical transactions. causal/hardware_acceptance
remain false. A partial failed group's attempt count can be unknown; inspect its
ordinary artifacts instead of inferring that no activity occurred.

SIGINT/SIGTERM request whole-batch stop through the ordinary engine. On platforms
with SIGUSR1, that signal requests one attempt skip through the existing skip
Event; repeated pending requests may coalesce. It does not skip an entire group.
Infrastructure or analysis errors halt before the next group. Kernel-report
candidates found by post-group analysis pause the batch as review_required; this
is conservative operator review, not bug confirmation or causal attribution.
This is not a real-time panic detector or proof that the host remains healthy.

A local batch lock prevents concurrent batches in the same results directory.
The existing executor owns the UDC lock for each group separately; this does not
promise uninterrupted ownership between groups or replace stand isolation.

Batch resume and panel controls are not implemented in this commit. progress.json
is an execution report, not a restart checkpoint. There is no automatic retry,
host recovery, replay of partial groups or automatic continuation after review.
Ordinary per-group checkpoints retain their existing strict compatibility rules.
The normal speed/backend restrictions remain; this does not integrate the
experimental SuperSpeed EP0 executor or add SuperSpeed data endpoints.
