# Reproducible variant corpus

Build a separate corpus without changing the checked-in seeds:

```sh
python -m usb_device_lab.corpus_variants --output var/corpus-50 --count 50 --seed 42
```

`--count` includes the original model: 1 original + 49 distinct variants by default.
The source profiles come from `corpus/manifest.toml`; the tool does not invent
additional device classes. With 24 input profiles, a successful build contains
1200 configurations, not 1200 different hardware device types.

Distinctness is calculated from `speed`, `descriptors`, `runtime`, and `protocol`.
Changes to metadata or the wire-mutator seed alone do not count as new models.
Each variant is constructed by the existing Mutator and validated by DeviceConfig.
This is model validation only: it does not establish USB class compliance,
SuperSpeed support, successful enumeration, or hardware compatibility.

The output contains `seeds/`, `manifest.toml`, `inventory.json` with parent digests
and mutation traces, and `complete.json`. A failed build can leave partial output:
use only a build whose process succeeded and whose completion marker exists.
Use a new output directory for every build; existing output is never overwritten.
The retry budget is finite and failure to reach the requested count is an error,
not a silently shortened corpus. Reproduction requires the same source contents,
software/Python versions, count, and seed.

## Using the generated corpus

In the lab configuration, set `corpus.seed_dir` to the absolute path of the new
`seeds` directory and `corpus.manifest` to the absolute path of its manifest.
Each original profile becomes a selectable family, for example `hid-keyboard`;
its generated profiles are `v0000` through `v0049`. Select this family to run
its 50 models. Set campaign.iterations to at least 50 to visit all 50 seeds.
The existing campaign limit remains 128 selected seeds: do not select all 1200
in one campaign. Select one device group at a time, or a compatible subset.

This commit adds the builder CLI, not web controls, skip controls, a larger
campaign scheduler, new device class implementations, or USB 3.x support.
