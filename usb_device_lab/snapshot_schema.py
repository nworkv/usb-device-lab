"""Strict decoding of version-1 supervisor snapshots; never starts campaigns."""
import json
import math


STATES = {'idle', 'starting', 'running', 'stopping', 'completed', 'stopped', 'failed', 'interrupted'}
PARAMETERS = {'iterations', 'seconds', 'seed', 'families', 'profiles'}
STATUS_FIELDS = {'state', 'session', 'runs', 'error', 'last_run', 'persistence_error'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate snapshot key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite snapshot number')


def bounded_string(value, maximum, nullable=False):
    return (nullable and value is None) or (type(value) is str and len(value) <= maximum)


def decode_snapshot(raw, identity):
    if type(raw) is not bytes or len(raw) > 65536:
        raise ValueError('Invalid snapshot bytes or size')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid snapshot encoding or nesting') from error
    if type(data) is not dict or set(data) != {'version', 'identity', 'status', 'parameters'}:
        raise ValueError('Invalid snapshot fields')
    if type(data['version']) is not int or data['version'] != 1 or data['identity'] != identity:
        raise ValueError('Incompatible snapshot version or lab identity')
    status, parameters = data['status'], data['parameters']
    if type(status) is not dict or not {'state', 'session', 'runs', 'error'} <= set(status) or set(status) - STATUS_FIELDS:
        raise ValueError('Invalid status fields')
    if type(status['state']) is not str or status['state'] not in STATES:
        raise ValueError('Invalid supervisor state')
    if type(status['runs']) is not int or not 0 <= status['runs'] < 2**63:
        raise ValueError('Invalid run count')
    if not bounded_string(status['session'], 128, nullable=True) or not bounded_string(status['error'], 4096, nullable=True):
        raise ValueError('Invalid session or error')
    for key, maximum in (('last_run', 128), ('persistence_error', 4096)):
        if key in status and not bounded_string(status[key], maximum):
            raise ValueError('Invalid status metadata')
    if parameters is not None:
        if type(parameters) is not dict or set(parameters) != PARAMETERS:
            raise ValueError('Invalid saved parameter fields')
        iterations, seconds, seed = (parameters[key] for key in ('iterations', 'seconds', 'seed'))
        if type(iterations) is not int or not 1 <= iterations <= 1000000:
            raise ValueError('Invalid saved iterations')
        if type(seconds) not in (int, float) or not 0 < seconds <= 300 or not math.isfinite(seconds):
            raise ValueError('Invalid saved seconds')
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError('Invalid saved seed')
        for key in ('families', 'profiles'):
            names = parameters[key]
            if type(names) is not list or len(names) > 128 or any(type(name) is not str or not 1 <= len(name) <= 127 for name in names):
                raise ValueError('Invalid saved corpus selectors')
        parameters = dict(parameters, families=tuple(parameters['families']), profiles=tuple(parameters['profiles']))
    return dict(status), parameters
