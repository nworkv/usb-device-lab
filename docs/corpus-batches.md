# Sequential corpus batches

Existing seeds run once each, family by family, with at most 128 parents per
group and at most 4096 planned attempts in the batch. Generated families map to
source profiles: 22 groups of 50 variants schedule 1100 attempts. The runner
does not generate variants, add structured mutations beyond those already in
the seeds, or execute groups in parallel. Per-seed WireMutator settings still apply.

## Research panel

Normal dual_cli serve now exposes a Batch Run panel. Choose whether to use all
groups of the active corpus or the current family/profile filters, build a plan,
then explicitly confirm the displayed attempt count to start. Planning does not
create output, connect telemetry or launch USB. The panel uses the active
in-memory corpus and timing settings, including a generated corpus activation.

The preview token is single-use. Starting checks the captured configuration
against current memory and disk configuration. run_batch compares a fresh full
plan with the approved selection before creating output or invoking the pipeline.
The ordinary pipeline also checks each expected group before preflight and
preparation; existing preparation verifies source digests again. Changed inputs
require a new plan rather than silently changing execution targets.

Common Stop/Skip buttons act on the batch stop Event and current attempt skip
Event. Skip is one attempt, not an entire group. Existing capture/campaign
history, per-attempt cards and artifact routes are retained for each group.
Global progress combines completed groups and the current group's committed
checkpoint prefix; it is not a count of confirmed USB transactions. Group rows
show recorded results and intentional skips separately. Partial failed-group
counts may be unknown. causal/hardware_acceptance remain false.

Infrastructure/analysis errors stop before the next group. Post-group kernel
report candidates produce review_required and require operator inspection.
This is not real-time panic detection, bug confirmation, causal attribution,
proof of host health, or automatic host recovery.

Batch resume is not implemented. The top Continue Selected action remains an
ordinary single-campaign resume, not batch resume. A new batch start processes
the selected seeds from the beginning. Do not use it as an automatic retry of
a partial or review-required batch. The normal speed restrictions remain;
experimental SuperSpeed EP0 execution is not integrated into this path.

## CLI

```sh
python -m usb_device_lab.batch_campaign --config "$HOME/.config/usb-device-lab/lab.dual.toml" --all-groups
python -m usb_device_lab.batch_campaign --config "$HOME/.config/usb-device-lab/lab.dual.toml" --all-groups --execute
```

CLI defaults to plan-only; --execute explicitly enables USB. It reads TOML,
not panel-only activation/overrides. --all-groups clears configured filters.
--mode uart/ssh selects a configured transport. Each group's iteration budget
is its seed count, not campaign.iterations. Timing/quiet windows use the config.
SIGINT/SIGTERM request whole-batch stop; SIGUSR1, where available, requests a
single skip and repeated pending requests may coalesce.

## Reports and locking

Atomic plan.json/progress.json live under results_dir/batches/<id>/. Source
paths and digests are recorded. Ordinary per-group campaign/capture artifacts
remain in their usual locations, linked by campaign_id. progress.json is a
report, not a batch restart checkpoint. No automatic replay or retry is added.
The local batch lock prevents concurrent batches in one results directory;
the existing UDC lock is held separately for each group, not continuously
across the whole batch. Stand isolation and hardware acceptance remain required.
