"""Strict old-checkpoint migration when only a finite SSH lifetime becomes zero."""
from dataclasses import replace
import json
import os
import re
import stat
from . import uart_checkpoint as checkpoint


def restore(path, config, parents, fingerprinter):
    current = fingerprinter(config, parents)
    if config.telemetry_mode != 'ssh' or config.ssh.lease_seconds != 0:
        return checkpoint.load(path, current, config.iterations)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ValueError('Duplicate checkpoint key')
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Checkpoint must be regular')
        raw = stream.read(65537)
    if len(raw) > 65536:raise ValueError('Checkpoint exceeds 64 KiB')
    value = json.loads(raw, object_pairs_hook=pairs)
    previous = value.get('fingerprint') if type(value) is dict else None
    if type(previous) is not str or re.fullmatch(r'[0-9a-f]{64}', previous) is None:
        raise ValueError('Invalid checkpoint fingerprint')
    if previous == current:return checkpoint.load(path, current, config.iterations)
    # Enumerate only the historical allowed finite lease values. All other input
    # settings, parents, Python version, target, budget and PRNG checks stay strict.
    for lease in (600, 3600, *[n for n in range(30, 3601) if n not in (600, 3600)]):
        old = replace(config, ssh=replace(config.ssh, lease_seconds=lease))
        legacy = fingerprinter(old, parents)
        if legacy == previous:
            restored = checkpoint.load(path, legacy, config.iterations)
            restored['ssh_lifetime_migrated_from'] = lease
            return restored
    return checkpoint.load(path, current, config.iterations)
