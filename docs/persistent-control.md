# Durable supervisor state

Start the persistent version of the local panel with:

```sh
python -m usb_device_lab.persistent_control --config lab.toml --port 8765
```

The existing control_http entry point remains unchanged and in-memory. This new
entry point reuses the same loopback server, API, token and Host/Origin policy.
Only run one supervisor for each results_dir.

The snapshot is results_dir/supervisor-state.json (schema version 1). An exclusive
supervisor.lock is held for the instance lifetime. Snapshots use a mode-0600 temp
file, file fsync, atomic replace and directory fsync. The snapshot contains status,
last run identifier and previous campaign parameters, never the bearer token.
Identity binds the config path, host address, USB bus and UDC name. An incompatible
or malformed snapshot fails startup instead of silently discarding history.

Active states recovered after an unclean exit become interrupted. No campaign is
automatically started. Inspect the lab and explicitly use resume. Resume creates
a new session with the previous parameters and existing corpus; it does not
restore a PRNG checkpoint. Runtime state changes are persisted after start,
completed attempts, stop and terminal status. A storage failure becomes visible
as persistence_error and requests cooperative stop; further starts are blocked
until storage is repaired and the supervisor restarted.

The advisory owner lock protects cooperating persistent supervisors sharing one
results directory, not every process that might access the same physical UDC.
The underlying campaign.lock still protects the campaign results directory.
Do not delete lock files to bypass an active owner. After repair, inspect any old
snapshot before resume; a failed save can leave the previous snapshot intact.

Tests:

```sh
python -m unittest discover -s tests -p 'test_persistent_control.py'
```

Eight targeted tests exercise reopen/resume, interrupted-state recovery, owner
locking, corrupt snapshots, lab identity mismatch, failed replace, worker close
and mode-0600 permissions. The preparation run uses the inspected supervisor
class in an isolated package with fake corpus selection and backend; the full
repository suite, SSH, KCOV and physical UDC have not been tested here.

The repository workflow skill is .agents/skills/usb-device-lab-commit/SKILL.md.
It is a reusable procedure, not an automatically installed Perplexity feature.
