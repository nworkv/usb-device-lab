"""Validated JSON checkpoints at completed-attempt boundaries; no campaign wiring."""
import copy
import hashlib
import json
import math
import os
import random
import re
import stat
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 524288
FIELDS = {'version', 'runtime', 'identity', 'engine_id', 'campaign_id', 'parameters',
          'corpus', 'next_iteration', 'last_run', 'selection_rng', 'mutation_rng'}


def runtime_id():
    return {'implementation': sys.implementation.name, 'python': list(sys.version_info[:3]), 'random_state': 3}


def hex_id(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def parameters(value):
    if type(value) is not dict or set(value) != {'iterations', 'seconds', 'seed', 'families', 'profiles'}:
        raise ValueError('Invalid checkpoint parameters')
    result = copy.deepcopy(value)
    if type(result['iterations']) is not int or not 1 <= result['iterations'] <= 1000000:
        raise ValueError('Invalid iterations')
    seconds = result['seconds']
    if type(seconds) not in (int, float) or not 0 < seconds <= 300 or not math.isfinite(seconds):
        raise ValueError('Invalid seconds')
    result['seconds'] = float(seconds)
    if type(result['seed']) is not int or not 0 <= result['seed'] < 2**63:
        raise ValueError('Invalid seed')
    for key in ('families', 'profiles'):
        names = result[key]
        if type(names) not in (list, tuple) or len(names) > 128 or any(type(name) is not str or not 1 <= len(name) <= 127 for name in names):
            raise ValueError('Invalid corpus selectors')
        result[key] = list(names)
    return result


def corpus_ids(value):
    if type(value) not in (list, tuple) or not 1 <= len(value) <= 4096 or any(not hex_id(ident, 64) for ident in value):
        raise ValueError('Invalid checkpoint corpus')
    if len(set(value)) != len(value):
        raise ValueError('Duplicate corpus digest')
    return sorted(value)


def rng_json(generator):
    if type(generator) is not random.Random:
        raise ValueError('A random.Random instance is required')
    version, mt, gaussian = generator.getstate()
    value = {'version': version, 'mt': list(mt), 'gaussian': gaussian}
    rng_state(value)
    return value


def rng_state(value):
    if type(value) is not dict or set(value) != {'version', 'mt', 'gaussian'} or type(value['version']) is not int or value['version'] != 3:
        raise ValueError('Invalid RNG state version')
    mt, gaussian = value['mt'], value['gaussian']
    if type(mt) is not list or len(mt) != 625 or any(type(word) is not int or not 0 <= word < 2**32 for word in mt[:-1]):
        raise ValueError('Invalid MT state words')
    if type(mt[-1]) is not int or not 0 <= mt[-1] <= 624 or not any(mt[:-1]):
        raise ValueError('Invalid MT index or zero state')
    if gaussian is not None and (type(gaussian) is not float or not math.isfinite(gaussian)):
        raise ValueError('Invalid Gaussian cache')
    state = (3, tuple(mt), gaussian)
    random.Random().setstate(state)
    return state


def validate(value):
    if type(value) is not dict or set(value) != FIELDS or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Invalid checkpoint schema')
    if canonical(value['runtime']) != canonical(runtime_id()):
        raise ValueError('Checkpoint runtime mismatch')
    if not hex_id(value['identity'], 64) or not hex_id(value['engine_id'], 64) or not hex_id(value['campaign_id'], 32):
        raise ValueError('Invalid checkpoint identity')
    normalized = parameters(value['parameters'])
    if canonical(normalized) != canonical(value['parameters']):
        raise ValueError('Noncanonical checkpoint parameters')
    if type(value['corpus']) is not list or value['corpus'] != corpus_ids(value['corpus']):
        raise ValueError('Noncanonical checkpoint corpus')
    iteration = value['next_iteration']
    if type(iteration) is not int or not 0 <= iteration <= normalized['iterations']:
        raise ValueError('Invalid iteration boundary')
    if iteration == 0 and value['last_run'] is not None or iteration > 0 and not hex_id(value['last_run'], 32):
        raise ValueError('Invalid completed-run boundary')
    rng_state(value['selection_rng'])
    rng_state(value['mutation_rng'])
    return copy.deepcopy(value)


def capture_checkpoint(identity, engine_id, campaign_id, campaign_parameters, corpus,
                       next_iteration, last_run, selection_rng, mutation_rng):
    if selection_rng is mutation_rng:
        raise ValueError('Selection and mutation generators must be independent')
    return validate({'version': 1, 'runtime': runtime_id(), 'identity': identity, 'engine_id': engine_id,
                     'campaign_id': campaign_id, 'parameters': parameters(campaign_parameters),
                     'corpus': corpus_ids(corpus), 'next_iteration': next_iteration, 'last_run': last_run,
                     'selection_rng': rng_json(selection_rng), 'mutation_rng': rng_json(mutation_rng)})


def encode_checkpoint(value):
    checked = validate(value)
    encoded = canonical({'checkpoint': checked, 'sha256': hashlib.sha256(canonical(checked)).hexdigest()})
    if len(encoded) > MAX_BYTES:
        raise ValueError('Checkpoint too large')
    return encoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate checkpoint JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite checkpoint JSON number')


def decode_checkpoint(raw, *, expected_identity, expected_engine_id, expected_parameters, expected_corpus):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('Invalid checkpoint bytes or size')
    try:
        envelope = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
        if type(envelope) is not dict or set(envelope) != {'checkpoint', 'sha256'} or not hex_id(envelope['sha256'], 64):
            raise ValueError('Invalid checkpoint envelope')
        value = validate(envelope['checkpoint'])
        if hashlib.sha256(canonical(value)).hexdigest() != envelope['sha256']:
            raise ValueError('Checkpoint checksum mismatch')
        if value['identity'] != expected_identity or value['engine_id'] != expected_engine_id:
            raise ValueError('Checkpoint lab or mutation engine mismatch')
        if canonical(value['parameters']) != canonical(parameters(expected_parameters)) or value['corpus'] != corpus_ids(expected_corpus):
            raise ValueError('Checkpoint campaign parameters or corpus mismatch')
    except (UnicodeError, RecursionError, OverflowError, TypeError, KeyError) as error:
        raise ValueError('Invalid checkpoint encoding or values') from error
    return value


def restore_generators(value):
    value = validate(value)
    selection, mutation = random.Random(), random.Random()
    selection.setstate(rng_state(value['selection_rng']))
    mutation.setstate(rng_state(value['mutation_rng']))
    return selection, mutation


def save_checkpoint(path, value):
    encoded = encode_checkpoint(value)
    path = Path(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.checkpoint-', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def load_checkpoint(path, **expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('Checkpoint is not a bounded regular file')
        raw = os.pread(fd, MAX_BYTES + 1, 0)
    finally:
        os.close(fd)
    return decode_checkpoint(raw, **expected)
