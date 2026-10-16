"""Build a separate reproducible corpus: N distinct USB models per source profile."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
from .model import DeviceConfig
from .mutator import Mutator

NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
MAX_FILE = 262144


def usb_digest(device):
    data = {key: device.data.get(key) for key in ('speed', 'descriptors', 'runtime', 'protocol')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def source_profiles(seed_dir, manifest):
    with Path(manifest).open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Manifest exceeds 64 KiB')
    parsed = tomllib.loads(raw.decode('utf-8'))
    if parsed.get('corpus', {}).get('format') != 1:
        raise ValueError('Unsupported corpus manifest')
    rows, groups = [], set()
    root = Path(seed_dir).resolve(strict=True)
    for family in parsed['family']:
        for profile in family['profiles']:
            name = family['name']
            if type(name) is not str or type(profile) is not str or not NAME.fullmatch(name) or not NAME.fullmatch(profile):
                raise ValueError('Invalid family/profile name')
            group = name + '-' + profile
            if not NAME.fullmatch(group) or group in groups:
                raise ValueError('Duplicate or overlong generated device group')
            groups.add(group)
            path = root / (group + '.json')
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
                raise ValueError('Seed must be inside seed_dir and not a symlink')
            with path.open('rb') as stream:
                payload = stream.read(MAX_FILE + 1)
            if len(payload) > MAX_FILE:
                raise ValueError('Seed exceeds 256 KiB')
            rows.append((group, DeviceConfig(json.loads(payload))))
            if len(rows) > 128:
                raise ValueError('At most 128 source profiles supported')
    if not rows:
        raise ValueError('No source profiles')
    return sorted(rows, key=lambda row: row[0])


def build(seed_dir, manifest, output, count=50, seed=42):
    if type(count) is not int or not 1 <= count <= 128:
        raise ValueError('count must be 1..128 (including the original)')
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError('seed must be 0..2**63-1')
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError('Output must be a new directory')
    parents = source_profiles(seed_dir, manifest)
    if len(parents) * count > 4096:
        raise ValueError('Corpus exceeds 4096 configurations')
    output.mkdir(parents=True, exist_ok=False)
    seeds = output / 'seeds'
    seeds.mkdir()
    entries, sections = [], ['[corpus]', 'format = 1', 'seed_dir = "seeds"', '']
    for group, parent in parents:
        local_seed = int.from_bytes(hashlib.sha256((str(seed) + ':' + group).encode()).digest()[:8], 'big')
        mutator = Mutator(local_seed)
        seen, accepted, attempts, selectors = set(), 0, 0, []
        while accepted < count:
            if attempts >= count * 100:
                raise RuntimeError('Distinct USB-model budget exhausted for ' + group + '; output is incomplete')
            child, trace = (parent, []) if attempts == 0 else mutator.mutate(parent)
            attempts += 1
            identity = usb_digest(child)
            if identity in seen:
                continue
            payload = (child.canonical() + chr(10)).encode('utf-8')
            if len(payload) > MAX_FILE:
                continue
            seen.add(identity)
            selector = 'v%04d' % accepted
            filename = group + '-' + selector + '.json'
            with (seeds / filename).open('xb') as stream:
                stream.write(payload)
            entries.append(dict(group=group, profile=selector, path='seeds/' + filename,
                digest=child.digest, usb_digest=identity, parent_digest=parent.digest, mutation=trace))
            selectors.append(selector)
            accepted += 1
        sections.extend(['[[family]]', 'name = ' + json.dumps(group), 'profiles = ' + json.dumps(selectors), ''])
    with (output / 'manifest.toml').open('x', encoding='utf-8') as stream:
        stream.write(chr(10).join(sections))
    report = dict(schema_version=1, seed=seed, per_profile=count, source_profiles=len(parents),
        configurations=len(entries), entries=entries, validation='model_only_not_hardware_or_class_conformance')
    with (output / 'inventory.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write(chr(10))
    # Only this marker identifies a completed build. Never consume partial output.
    with (output / 'complete.json').open('x', encoding='utf-8') as stream:
        json.dump(dict(configurations=len(entries), source_profiles=len(parents), per_profile=count), stream)
        stream.write(chr(10))
    return {key: value for key, value in report.items() if key != 'entries'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-dir', default='corpus/seeds')
    parser.add_argument('--manifest', default='corpus/manifest.toml')
    parser.add_argument('--output', required=True)
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    try:
        result = build(args.seed_dir, args.manifest, args.output, args.count, args.seed)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(2, str(error) + chr(10))
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
