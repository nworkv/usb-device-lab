# Batch analysis contract

A completed group's result must carry a lowercase 32-hex campaign_id and an
analysis.summary object with:

- analysis_complete set to the literal boolean true;
- capture_id equal to the returned campaign_id;
- kernel_candidate_count present as a nonnegative integer (not boolean,
  floating-point, string or null).

If the capture callback already supplied a campaign ID, a different returned
ID is rejected without replacing the captured identity in the batch report.
Missing, malformed or incomplete analysis is not interpreted as zero reports.
It fails the batch before another group is started. Existing explicit execution
or analysis errors also halt the batch. An operator stop never starts a following
group and does not need a finished analysis result to stop.

A valid positive candidate count retains review_required. A valid zero count
only satisfies the software analysis contract; it does not prove host health,
USB enumeration, complete telemetry or absence of kernel bugs. Inspection is
post-group, not a real-time panic detector, and causality remains unknown.

This applies to both the CLI runner and the panel adapter using run_batch.
Report/checkpoint formats, mutation algorithms and hardware speed support are
unchanged. Ordinary per-group artifacts remain the evidence for partial or
failed execution. Full CI and stand acceptance are separate from isolated
contract tests.
