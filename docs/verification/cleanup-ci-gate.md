# Focused cleanup CI gate — 2026-10-21

Baseline: feature/uart-agentless at 5580392f4d17ec38f495c73a9055a8e835f3e28d. Existing tests.yml inspected (blob d7280abba23febbbb2a4bd7ae7ddb828e2cd5377).

Change: run the focused RPC, host-agent collector and runner-pipe suites from the actual CI checkout on Python 3.11/3.12. Emit a JSON report and upload it even after failure. Fail on unittest failure/error, zero tests, all-skipped selection, unexpected success, or captured parent-process ResourceWarning. Individual skipped/expected-failure tests are counted in the report. Preserve the full make test step and allow it after a failed cleanup gate unless the job is cancelled.

PASS: script syntax and six real subprocess self-checks with synthetic test directories: passing suite exits 0; failing suite, ResourceWarning, empty suite, all skipped and import error exit 1. Report JSON checked, including resource-warning capture and skip reason.

SKIPPED: actual focused project test run, full project regression and remote GitHub Actions execution. Only synthetic runner self-checks were executed locally; no claim that CI is green. Full checkout/dependency environment was not assembled here.
SKIPPED: KCOV, /dev/kmsg and USB hardware. The gate detects parent-process warnings only; subprocess stderr warnings are not automatically parsed.
