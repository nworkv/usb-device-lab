"""Classify run diagnostics while retaining the legacy errors list.

Kernel events are unconfirmed evidence, not proof of a reproducible bug.
"""
import hashlib
import re

SIGNALS = (
    ('panic', re.compile(r'\bKernel panic\b', re.I)),
    ('kasan', re.compile(r'\bKASAN:')),
    ('kfence', re.compile(r'\bKFENCE:')),
    ('kmsan', re.compile(r'\bKMSAN:')),
    ('ubsan', re.compile(r'\bUBSAN:')),
    ('kcsan', re.compile(r'\bKCSAN:')),
    ('oops', re.compile(r'\bOops:')),
    ('fault', re.compile(r'\bgeneral protection fault\b|\bBUG: unable to handle\b', re.I)),
    ('bug', re.compile(r'\bBUG:|\bkernel BUG\b')),
    ('warning', re.compile(r'\bWARNING:')),
    ('hung_task', re.compile(r'\bINFO: task .* blocked')),
)
ADDRESS = re.compile(r'0x[0-9a-fA-F]+|\+0x[0-9a-fA-F]+/0x[0-9a-fA-F]+|\+[0-9a-fA-F]+/[0-9a-fA-F]+')
INFRA_KINDS = {'infrastructure', 'cleanup', 'coverage_unavailable', 'coverage', 'log_loss'}
EXECUTOR_KINDS = {'executor', 'executor_io'}


def normalize_summary(text):
    return ADDRESS.sub('<addr>', text.strip())


def kernel_events(log):
    lines = log.splitlines()
    events = []
    for index, line in enumerate(lines):
        for kind, pattern in SIGNALS:
            match = pattern.search(line)
            if match is None:
                continue
            raw = line[match.start():].strip()
            summary = normalize_summary(raw)
            events.append({
                'kind': kind,
                'summary': summary,
                'raw_summary': raw,
                'header_fingerprint': hashlib.sha256((kind + ':' + summary).encode()).hexdigest(),
                'line_number': index + 1,
                'context': '\n'.join(lines[max(0, index - 3):index + 81]),
                'confirmation': 'unconfirmed',
            })
            break
    return events


def add_error(errors, kind, summary):
    if not any(e.get('kind') == kind and e.get('summary') == summary for e in errors):
        errors.append({'kind': kind, 'summary': summary})


def make_verdict(result):
    events = result.get('kernel_events', [])
    kinds = {e.get('kind') for e in result.get('errors', [])}
    if events:
        outcome = 'kernel_candidate'
    elif kinds & INFRA_KINDS:
        outcome = 'infrastructure_failure'
    elif kinds & EXECUTOR_KINDS:
        outcome = 'executor_failure'
    elif kinds:
        outcome = 'unclassified_failure'
    elif result.get('coverage_valid'):
        outcome = 'coverage_observed'
    else:
        outcome = 'inconclusive'
    return {
        'schema_version': 1,
        'outcome': outcome,
        'kernel_evidence_detected': bool(events),
        'confirmation': 'unconfirmed' if events else 'not_applicable',
        'kernel_event_count': len(events),
        'kernel_event_kinds': sorted({e['kind'] for e in events}),
        'coverage_valid': bool(result.get('coverage_valid')),
        'telemetry_complete': not result.get('log_dropped') and not bool(kinds & {'infrastructure', 'cleanup'}),
        'diagnostic_kinds': sorted(k for k in kinds if isinstance(k, str)),
    }


def classify(result):
    errors = list(result.get('errors', []))
    events = kernel_events(result.get('kernel_log', ''))
    for event in events:
        add_error(errors, 'kernel', event['summary'])
    for line in result.get('executor_log', '').splitlines():
        if '"kind":' in line and ('_error"' in line or '"protocol_error"' in line):
            add_error(errors, 'executor_io', line[:1024])
    if result.get('log_dropped'):
        add_error(errors, 'log_loss', 'kernel log records lost or truncated')
    if result.get('saturated'):
        result['coverage_valid'] = False
        add_error(errors, 'coverage', 'KCOV buffer saturated; coverage rejected')
    code = result.get('executor_returncode')
    expected_stop = bool(result.get('window_completed')) and code in (-15, -9)
    if code not in (0, None) and not expected_stop:
        add_error(errors, 'executor', 'executor exited with code %s' % code)
    if not result.get('pcs'):
        result['coverage_valid'] = False
        if not result.get('saturated') and not any(e.get('kind') in ('infrastructure', 'cleanup') for e in errors):
            add_error(errors, 'coverage_unavailable', 'no remote USB coverage observed; check bus and kernel instrumentation')
    result['errors'] = errors
    result['kernel_events'] = events
    result['verdict'] = make_verdict(result)
    return result
