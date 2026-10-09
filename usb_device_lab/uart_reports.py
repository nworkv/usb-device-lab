"""Bounded offline UART analysis. Associations are observational, never causal."""
import re
from dataclasses import dataclass

MAX_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 100000
HEADER = re.compile(rb'^(?:<\d+>)?(?:\[\s*[\d.]+\]\s*)?(BUG:|WARNING:|Oops:|Kernel panic|UBSAN:|KASAN:|KCSAN:)')

@dataclass(frozen=True)
class AttemptWindow:
    attempt_id: str
    capture_id: str
    start_ns: int
    end_ns: int
    active_start_ns: int
    active_end_ns: int


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected nonnegative integer')
    return value


def analyze(raw, chunks, windows, events=(), *, capture_id, context_lines=8, report_lines=256):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('Analysis requires bytes, at most 16 MiB; split larger captures upstream')
    if type(capture_id) is not str or not capture_id or len(capture_id) > 256:
        raise ValueError('Invalid capture identity')
    if type(chunks) not in (list, tuple) or len(chunks) > MAX_CHUNKS:
        raise ValueError('Too many chunks')
    if type(windows) not in (list, tuple) or len(windows) > 1024:
        raise ValueError('Too many windows')
    if type(events) not in (list, tuple) or len(events) > MAX_CHUNKS:
        raise ValueError('Too many events')
    if type(context_lines) is not int or not 0 <= context_lines <= 64 or type(report_lines) is not int or not 1 <= report_lines <= 4096:
        raise ValueError('Invalid parser limits')
    ids = set()
    for w in windows:
        if type(w) is not AttemptWindow or type(w.attempt_id) is not str or not w.attempt_id or len(w.attempt_id) > 256 or w.attempt_id in ids or w.capture_id != capture_id:
            raise ValueError('Invalid, duplicate or cross-capture attempt')
        values = [integer(v) for v in (w.start_ns, w.active_start_ns, w.active_end_ns, w.end_ns)]
        if values != sorted(values) or w.start_ns >= w.end_ns:
            raise ValueError('Invalid half-open attempt window')
        ids.add(w.attempt_id)
    end, previous_time, previous_epoch = 0, 0, 0
    for sequence, c in enumerate(chunks, 1):
        if type(c) is not dict:
            raise ValueError('Invalid chunk record')
        for key in ('sequence', 'offset_start', 'offset_end', 'byte_count', 'monotonic_ns', 'connection_epoch'):
            integer(c.get(key))
        if c['sequence'] != sequence or c['offset_start'] != end or not end < c['offset_end'] <= len(raw) or c['byte_count'] != c['offset_end'] - end or c['connection_epoch'] < 1 or c['connection_epoch'] < previous_epoch or c['monotonic_ns'] < previous_time:
            raise ValueError('Noncontiguous or inconsistent chunk index')
        end, previous_time, previous_epoch = c['offset_end'], c['monotonic_ns'], c['connection_epoch']
    chunk_boundaries = {0, end} | {c['offset_start'] for c in chunks}
    gaps = set()
    for event in events:
        if type(event) is not dict:
            raise ValueError('Invalid event record')
        if event.get('kind') in ('disconnected', 'open_failed', 'failed'):
            offset = integer(event.get('offset'))
            if offset not in chunk_boundaries:
                raise ValueError('Event outside indexed prefix')
            gaps.add(offset)
    boundaries = {0, end} | gaps
    for left, right in zip(chunks, chunks[1:]):
        if left['connection_epoch'] != right['connection_epoch']:
            boundaries.add(right['offset_start'])
    fragments = []
    for w in windows:
        ranges = []
        for c in chunks:
            if w.start_ns <= c['monotonic_ns'] < w.end_ns:
                if ranges and ranges[-1]['offset_end'] == c['offset_start'] and c['offset_start'] not in boundaries:
                    ranges[-1]['offset_end'] = c['offset_end']
                else:
                    ranges.append(dict(offset_start=c['offset_start'], offset_end=c['offset_end'], connection_epoch=c['connection_epoch']))
        overlap = [v.attempt_id for v in windows if v.attempt_id != w.attempt_id and max(v.start_ns, w.start_ns) < min(v.end_ns, w.end_ns)]
        fragments.append(dict(attempt_id=w.attempt_id, ranges=ranges, overlapping_attempts=overlap,
                              temporal_precision='chunk_receive_time', completeness='unknown'))
    reports = []
    def emit(lines, prefix, termination):
        if not lines:
            return
        if len(reports) >= 1024:
            raise ValueError('Too many reports in one analysis')
        start, finish = lines[0][0], lines[-1][1]
        relevant = [c for c in chunks if c['offset_start'] < finish and start < c['offset_end']]
        associations = []
        for w in windows:
            times = [c['monotonic_ns'] for c in relevant if w.start_ns <= c['monotonic_ns'] < w.end_ns]
            if times:
                phases = sorted(set('pre' if t < w.active_start_ns else 'active' if t < w.active_end_ns else 'post' for t in times))
                associations.append(dict(attempt_id=w.attempt_id, receive_phases=phases))
        text = b''.join(v[2] for v in lines).decode('utf-8', errors='backslashreplace')
        reports.append(dict(offset_start=start, offset_end=finish,
            context_offset_start=prefix[0][0] if prefix else start,
            context=b''.join(v[2] for v in prefix).decode('utf-8', errors='backslashreplace'), text=text,
            connection_epoch=relevant[0]['connection_epoch'], termination=termination,
            associations=associations, association='ambiguous' if len(associations) > 1 else 'candidate' if associations else 'unassigned',
            causal=False, completeness='unknown', truncated=termination != 'end_trace_marker')))
    points = sorted(boundaries)
    for a, b in zip(points, points[1:]):
        history, current, prefix = [], [], []
        offset = a
        for match in re.finditer(rb'[^\r\n]*(?:\r\n|\r|\n|$)', raw[a:b]):
            data = match.group()
            if not data:
                continue
            line = (offset, offset + len(data), data)
            offset += len(data)
            header = HEADER.match(data.lstrip())
            if header:
                emit(current, prefix, 'next_header')
                current, prefix = [line], list(history)
            elif current:
                current.append(line)
            if current and b'end trace' in data.lower():
                emit(current, prefix, 'end_trace_marker')
                current = []
            elif current and len(current) >= report_lines:
                emit(current, prefix, 'limit')
                current = []
            if context_lines:
                history = (history + [line])[-context_lines:]
        emit(current, prefix, 'capture_end' if b == end else 'gap')
    return dict(capture_id=capture_id, indexed_bytes=end, unindexed_tail_bytes=len(raw)-end,
                fragments=fragments, reports=reports, completeness='unknown', causal=False)
