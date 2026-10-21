# Cleanup gate discovery diagnostics — 2026-10-21

Target: feature/uart-agentless after ba4e138e2de09db0c802509fabd91fe663476c1b.

Change: report infrastructure_error and return failure when discovery/runner raises an ordinary exception. Still attempt final garbage collection and JSON report generation. Missing/unimportable test directories no longer escape before report creation. Report write failures and process termination are not swallowed or guaranteed reportable.

Add persistent tests/test_cleanup_gate.py. Its name intentionally falls outside the focused test_rpc patterns to avoid recursively launching the gate; the full make test discovery can run it.

PASS: eight tests ran the proposed script in real Python subprocesses with temporary synthetic test directories. Covered success, test failure, ResourceWarning, zero tests, all skipped with reason, import error, missing directory with infrastructure_error, and unexpected success. All eight passed and each invocation produced parseable report JSON.

SKIPPED: actual project focused gate, full regression and remote GitHub Actions status. No full project checkout/dependency environment was assembled here. USB/KCOV hardware not exercised.
