# Package 2: assembly and first-run handoff — 2026-10-21

Baseline: feature/uart-agentless after 8ac948ff1195cc1b910fb1eda58d493d2b4f7ac5.
Reviewed pi_dual_setup.sh (356ec13589979cfc1453f938409b6a259e20a034), dual_cli.py (57368e8aa0d60d04440281a09d1d4e489571569e), dual_init.py (c68233c4d8a9e14722fae6b17eb38bd1d920ef7a), examples/lab.dual.example.toml (914239e56b25cdee07d47b1c5f41a3341128ade1), Makefile and existing UART/SSH setup guides.

Changes: run the cleanup gate in pi_dual_setup.sh before config generation/preflight; save its report beside UART regression reports. Replace the obsolete patch-only missing-entry message with a current-checkout diagnostic. Add assembly-first-run-ru.md for current checkout installation without reapplying old patches, finite first acceptance, preserved-config behavior, explicit SSH lease_seconds=0 semantics and open validation limits.

PASS: bash -n on proposed setup script and all 7 shell blocks in the new guide. Static check confirmed cleanup gate precedes config generation. Commands and options were cross-checked against the fetched CLI/init source. No install commands were executed.

SKIPPED: full repository/CI tests, apt/pip/sudo installation, boot/module changes, real UART/SSH access, actual KCOV, /dev/kmsg and USB hardware acceptance. These remain pending, not passed. This package completes the planned preparation work, not device validation.
