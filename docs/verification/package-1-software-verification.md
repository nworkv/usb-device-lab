# Package 1: software verification — 2026-10-21

Baseline: nworkv/usb-device-lab feature/uart-agentless, 55dac6d3f7bfe8cb673d5757b10be7e3a9095ef7.
Status: PARTIAL PASS; full-project and hardware validation remain pending.

## Executed

A temporary partial source tree was assembled. These production files were transcribed from the connector results and matched their Git blob hashes exactly:
- usb_device_lab/rpc.py: f35a203a42a7de97c229bbc7c61090a5a16b8a21
- usb_device_lab/host_agent.py: 7f447c0068d1358a6cf677340c14ff3b4d4c9dcc
- usb_device_lab/host_logs.py: 988e6a8f2726b52468171faec8c22c2d257190c8

17 targeted checks passed together in a fresh Python process: 8 RPC send checks, 6 host-agent EOF/error checks, and 3 collector-result integration checks. The integration checks used real Python child processes and pipes; KernelLogs was replaced with a fake. No failures or errors were observed. A second run with a reconstructed cleanup gate passed the same 17 checks and captured zero parent-process ResourceWarning messages. This is 17 distinct checks, not 34.

Important limitations: the test harnesses were adapted from the fetched test files and were not byte-identical copies. The temporary package initializer was empty. The reconstructed gate was not the exact repository script. This is not evidence that the original focused suite or full project passes. No production code changes were justified by this executed scope.

## Skipped and still open

- Full git checkout and make test. No local checkout was present; the execution environment cannot use network access, and available connector tools do not provide checkout execution.
- Actual repository cleanup gate and exact original focused suites.
- Complete runtime/web and runner suites.
- Remote GitHub Actions status and report artifacts; available connector tools did not expose Actions run inspection.
- Actual KernelLogs /dev/kmsg, KCOV collector and USB hardware.

## Handoff

Package 1 execution ends with these limitations explicitly retained, not converted to passed checks. Package 2 remains: review setup/configuration/docs for assembly and first boot. Software/hardware readiness is not certified by this report.
