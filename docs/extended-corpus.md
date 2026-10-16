# Extended corpus and operator generation

## Research panel

The normal dual_cli serve entry point now includes a Create Corpus panel.
Choose standard or extended baselines, 1..50 configurations per profile
(including its original), and a seed. Generation uses the entire original
TOML corpus manifest, not the selected family/profile filters or an already
activated generated corpus. Extended input must contain hid/keyboard and
hid/consumer-control with matching baseline metadata.

Generation runs in a separate thread, creates at most 2048 variant files,
and writes only under corpus_out/generated/<job-id>/. No browser-supplied
output paths or commands are accepted. The generated directory must be owned
by the service user and not group/world writable. Builds and USB campaigns,
analysis, replay and configuration changes cannot run concurrently in this panel.

This version reports build phases and planned/final counts, not percentage
progress. It does not cancel a running build. The Stop Attempts action is
rejected during generation rather than pretending it stopped the builder.
Wait for completion before shutting down the panel. Shutdown waits up to 30
seconds; forced exit or filesystem failure can leave incomplete output.

A successful builder result and matching complete.json are required before
publication. Activation is a separate explicit action and never starts USB.
It selects the first generated group as a safe default instead of selecting
all 950/1100 seeds. Existing family controls can then choose another group.
The campaign's 128-selected-seed limit remains. Set iterations to at least 50
to visit every seed in a 50-entry group.

Generated corpus activation and timing overrides are in memory; TOML is not
rewritten. Return to TOML restores the original corpus selection. The panel
keeps only the latest generation record in the session; files remain on disk
across restarts and can be configured manually. Only a completed current-session
job ID can be activated through the API. Normal request authentication and
Host/Origin guards also apply to generation and activation.

Extended generation adds hid/keyboard-low-speed,
hid/consumer-control-low-speed and vendor/bulk-loopback. With the current
19-profile input this means 22 baselines and, for count=50, 1100 configurations,
not 1100 device types. The two Low Speed baselines use EP0 maximum packet size
8 and compatible interrupt runtimes. Vendor loopback queues host OUT 0x01 data
for IN 0x81. Software model and script tests are not hardware acceptance.
This does not enable experimental SuperSpeed in normal campaigns.

## CLI equivalents

```sh
python -m usb_device_lab.extended_corpus --output var/extended-corpus
python -m usb_device_lab.corpus_variants --seed-dir var/extended-corpus/seeds --manifest var/extended-corpus/manifest.toml --output var/extended-corpus-50 --count 50 --seed 42
```

CLI output includes seeds/, manifest.toml, inventory.json and complete.json.
Existing output is refused and checked-in seeds are not overwritten. Use only
a successful build with its marker. CLI paths can be set explicitly in TOML;
the panel does not accept arbitrary activation paths.
