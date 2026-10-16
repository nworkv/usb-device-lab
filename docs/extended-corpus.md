# Extended baseline corpus

Build into a new directory, without changing checked-in seeds or lab TOML:

```sh
python -m usb_device_lab.extended_corpus --output var/extended-corpus
```

The input must be a baseline manifest containing hid/keyboard and
hid/consumer-control, with matching seed metadata. The builder copies its input
profiles and adds hid/keyboard-low-speed, hid/consumer-control-low-speed, and
vendor/bulk-loopback. With the current 19-profile input, a successful build has
22 baseline profiles. This is not a complete inventory of USB device classes.

The two Low Speed baselines use speed=1, bcdUSB 0x0110, EP0 maximum packet size
8, compatible interrupt runtimes and English-US string selectors. Vendor
loopback uses Full Speed, OUT 0x01 and IN 0x81, with a script rule that queues
the received payload for IN. Host-side OUT and IN transfers are required; this
is not an automatically driven Linux device-class implementation. Model and
script-queue tests do not establish hardware compatibility or enumeration.

The output includes seeds/, manifest.toml, inventory.json with digests, and
complete.json written last. Existing output is refused. A failed filesystem
write can leave incomplete output: use only a successful build with its marker.
Source files are not overwritten. This does not alter scripts/gen_seeds.py or
the default checked-in manifest, nor enable experimental SuperSpeed.

## Generate 50 variants per extended profile

```sh
python -m usb_device_lab.corpus_variants --seed-dir var/extended-corpus/seeds --manifest var/extended-corpus/manifest.toml --output var/extended-corpus-50 --count 50 --seed 42
```

For 22 input profiles a successful variant build contains 1100 configurations,
not 1100 device types. The count includes each original baseline. Select a
generated group such as hid-keyboard-low-speed in the existing corpus panel
after setting corpus.seed_dir and corpus.manifest to the output's absolute
paths. A campaign still accepts at most 128 selected seeds. For all 50 entries
in one group, set iterations to at least 50. Generation is CLI-only; the panel
selects an already configured corpus and does not create it.
