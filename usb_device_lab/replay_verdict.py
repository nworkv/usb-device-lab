def summarize_replays(expected, observations, requested):
    # Caller supplies validated header signatures and per-attempt validity.
    expected = set(expected)
    if not expected or type(requested) is not int or requested < 2:
        raise ValueError('candidate signatures and at least two attempts required')
    if len(observations) > requested:
        raise ValueError('more observations than requested attempts')
    counts = {key: 0 for key in expected}
    valid = 0
    for item in observations:
        if item.get('valid') is not True:
            continue
        valid += 1
        for key in expected.intersection(item.get('signatures', [])):
            counts[key] += 1
    reproduced = sorted(key for key, count in counts.items() if count >= 2)
    state = ('reproduced' if reproduced else 'inconclusive' if valid != requested
             else 'flaky' if any(counts.values()) else 'not_reproduced')
    return {'schema_version': 1, 'confirmation': state,
            'requested': requested, 'attempted': len(observations), 'usable': valid,
            'matching_replays': counts, 'reproduced_signatures': reproduced,
            'limitation': 'Matching reports do not prove a unique vulnerability or root cause.'}
