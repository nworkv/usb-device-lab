"""Versioned UART checkpoint: JSON only, strict compatibility, atomic replacement."""
import hashlib
import json
import math
import os
import random
import re
import stat
import sys
import uuid
from pathlib import Path

VERSION = 'usb-tree-model-valid-v3-round-robin'


def fingerprint(config, parents):
    data = dict(version=VERSION, python=list(sys.version_info[:2]), seeds=[p.digest for p in parents],
        udc=config.udc, driver=config.gadget_udc_driver, seed=config.seed, iterations=config.iterations,
        seconds=config.seconds, pre=config.pre_run_quiet_seconds, post=config.post_run_capture_seconds,
        mode=config.telemetry_mode, feedback=config.feedback, strategy=config.strategy, uart=vars(config.uart))
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def state(value):
    if type(value) not in (list, tuple) or len(value) != 3 or type(value[0]) is not int or value[0] != 3:
        raise ValueError('Unsupported PRNG state')
    words, gaussian = value[1], value[2]
    if type(words) not in (list, tuple) or len(words) != 625:
        raise ValueError('Invalid PRNG words')
    if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in words[:-1]) or type(words[-1]) is not int or not 0 <= words[-1] <= 624:
        raise ValueError('Invalid PRNG word/index')
    if gaussian is not None and (type(gaussian) not in (int, float) or not math.isfinite(gaussian)):
        raise ValueError('Invalid PRNG Gaussian cache')
    result = (3, tuple(words), gaussian)
    random.Random().setstate(result)
    return result


def load(path, expected, iterations):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate checkpoint key')
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Checkpoint must be a regular file')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Checkpoint exceeds 64 KiB')
    data = json.loads(raw, object_pairs_hook=pairs)
    if type(data) is not dict or set(data) != {'version', 'fingerprint', 'next_iteration', 'prng', 'source_capture'} or data['version'] != VERSION or data['fingerprint'] != expected:
        raise ValueError('Incompatible mutation engine or settings; start a new campaign. Saved configuration replay remains available')
    index = data['next_iteration']
    if type(index) is not int or not 0 <= index <= iterations or type(data['source_capture']) is not str or re.fullmatch(r'[0-9a-f]{32}', data['source_capture']) is None:
        raise ValueError('Invalid checkpoint progress/identity')
    data['prng'] = state(data['prng'])
    return data


def write(path, digest, next_iteration, rng, capture):
    path = Path(path)
    data = dict(version=VERSION, fingerprint=digest, next_iteration=next_iteration,
                prng=state(rng.getstate()), source_capture=capture)
    temporary = path.with_name('.checkpoint-' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, allow_nan=False)
            stream.write(chr(10))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
