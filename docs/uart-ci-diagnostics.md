# UART CI diagnostic reports

The workflow now retains installation/compilation/test/JS logs and report.json
as an Actions artifact for each Python matrix entry, even after a failed step.
Retention is seven days. pipefail preserves the failing command's exit code when
output is piped through tee. Existing UART test selection and job timeout remain.

```sh
python scripts/run_uart_tests.py --output uart-ci-results/report.json
python3 -m unittest discover -s tests -p 'test_uart_ci_report.py' -v
```

The runner records per-test IDs, statuses, durations and unittest tracebacks;
skips and expected failures remain explicit. Failure, error, unexpected success,
an empty suite or an aborted runner returns nonzero. Normal unittest success can
include skips/expected failures, so inspect those counts rather than treating them
as executed passing checks. Discovery exceptions become error tests.

No environment-variable dump or production artifact upload. However test error
messages/logs can contain data emitted by tests/dependencies; artifact permissions
follow repository/Actions access. Do not add real stand secrets to CI fixtures.

If installation or compilation fails before the runner, report.json may be absent;
read that step's retained log instead. Termination/power loss can prevent final
report/artifact creation. A report describes that run only, not hardware readiness.

Six reporter tests passed locally with synthetic passing/failing/error/subtest,
skipped/expected-failure/unexpected-success and empty suites. YAML parsed locally.
Full checkout UART tests and the previous Actions status were NOT verified in this
turn. No specific CI failure is claimed or fixed without a traceback. Product
UART behavior is unchanged; the purpose is to make subsequent failures inspectable.
