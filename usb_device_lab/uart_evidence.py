"""Offline, bounded campaign evidence export. Never rewrites original artifacts."""
import argparse
import json
import os
import re
import stat
import uuid
from pathlib import Path
from .uart_reports import AttemptWindow, analyze, MAX_BYTES, MAX_CHUNKS

MAX_META = 32 * 1024 * 1024


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def decode(data):
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    if type(value) is not dict:
        raise ValueError('JSON object required')
    return value


def read_file(directory_fd, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('Not a bounded regular file: ' + name)
        parts, size = [], 0
        while True:
            data = os.read(fd, min(65536, limit + 1 - size))
            if not data:
                break
            parts.append(data)
            size += len(data)
            if size > limit:
                raise ValueError('File exceeded limit while reading: ' + name)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Input changed while reading; stop the campaign first')
        return b''.join(parts)
    finally:
        os.close(fd)


def jsonl(data):
    records, offset = [], 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            return records, len(data) - offset
        if len(line) > 65536 or len(records) >= MAX_CHUNKS:
            raise ValueError('JSONL record/count limit exceeded')
        records.append(decode(line))
        offset += len(line)
    return records, 0


def export_campaign(campaign, capture_root):
    campaign, capture_root = Path(campaign), Path(capture_root)
    root_fd = os.open(campaign, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    capture_fd, output_fd = None, None
    try:
        metadata = decode(read_file(root_fd, 'campaign.json', 65536))
        ident = metadata.get('capture_id')
        if type(ident) is not str or re.fullmatch(r'[0-9a-f]{32}', ident) is None or campaign.name != ident:
            raise ValueError('Invalid campaign/capture identity')
        if type(metadata.get('schema_version')) is not int or metadata['schema_version'] != 1 or metadata.get('mode') != 'uart' or metadata.get('feedback') != 'none':
            raise ValueError('Not a stage-4 UART campaign')
        capture_parent = os.open(capture_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            capture_fd = os.open(ident, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=capture_parent)
        finally:
            os.close(capture_parent)
        chunks, chunk_tail = jsonl(read_file(capture_fd, 'chunks.jsonl', MAX_META))
        events, event_tail = jsonl(read_file(capture_fd, 'events.jsonl', MAX_META))
        raw = read_file(capture_fd, 'uart.raw', MAX_BYTES)
        windows, attempts, diagnostics = [], [], []
        names = sorted(name for name in os.listdir(root_fd) if name.startswith('attempt-'))
        if len(names) > 1024:
            raise ValueError('Evidence export supports at most 1024 attempts')
        for name in names:
            if re.fullmatch(r'attempt-[0-9]{6}', name) is None:
                raise ValueError('Invalid attempt directory name')
            attempt_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
            try:
                started = decode(read_file(attempt_fd, 'started.json', 65536))
                if started.get('capture_id') != ident or started.get('attempt_id') != name:
                    raise ValueError('Attempt identity mismatch')
                attempt = dict(attempt_id=name, result_available=False, window_available=False)
                try:
                    window = AttemptWindow(**decode(read_file(attempt_fd, 'window.json', 65536)))
                except FileNotFoundError:
                    diagnostics.append(dict(kind='attempt_incomplete', attempt_id=name, missing='window.json'))
                else:
                    if window.attempt_id != name or window.capture_id != ident or window.start_ns != started.get('start_ns'):
                        raise ValueError('Window identity/start mismatch')
                    windows.append(window)
                    attempt['window_available'] = True
                try:
                    result = decode(read_file(attempt_fd, 'result.json', 65536))
                except FileNotFoundError:
                    diagnostics.append(dict(kind='attempt_incomplete', attempt_id=name, missing='result.json'))
                else:
                    if result.get('capture_id') != ident:
                        raise ValueError('Result identity mismatch')
                    attempt['result_available'] = True
                    for key in ('window_completed', 'interrupted'):
                        if type(result.get(key)) is not bool:
                            raise ValueError('Invalid attempt status')
                        attempt[key] = result[key]
                    error = result.get('infrastructure_error')
                    if error is not None:
                        if type(error) is not str:
                            raise ValueError('Invalid infrastructure error')
                        diagnostics.append(dict(kind='campaign_infrastructure', attempt_id=name, summary=error))
                    code = result.get('executor_returncode')
                    if code is not None and type(code) is not int:
                        raise ValueError('Invalid executor return code')
                    if code not in (None, 0, -15, -9):
                        diagnostics.append(dict(kind='executor_exit', attempt_id=name, returncode=code))
                attempts.append(attempt)
            finally:
                os.close(attempt_fd)
        analysis = analyze(raw, chunks, windows, events, capture_id=ident)
        for event in events:
            if event.get('kind') in ('open_failed', 'disconnected', 'failed'):
                diagnostics.append(dict(kind='uart_capture', event=event, loss='unknown'))
        if chunk_tail or event_tail:
            diagnostics.append(dict(kind='metadata_tail', chunk_tail_bytes=chunk_tail, event_tail_bytes=event_tail))
        if analysis['unindexed_tail_bytes']:
            diagnostics.append(dict(kind='unindexed_raw_tail', byte_count=analysis['unindexed_tail_bytes']))
        report_ids = {a['attempt_id']: [] for a in attempts}
        for number, report in enumerate(analysis['reports']):
            report['report_id'] = 'report-%06d' % number
            report['kind'] = 'kernel_report_candidate'
            for association in report['associations']:
                report_ids[association['attempt_id']].append(report['report_id'])
        for attempt in attempts:
            attempt['candidate_reports'] = report_ids[attempt['attempt_id']]
        summary = dict(schema_version=1, capture_id=ident, completeness='unknown', causal=False,
            observation='uart_bytes_observed' if analysis['indexed_bytes'] else 'no_indexed_uart_bytes',
            indexed_bytes=analysis['indexed_bytes'], unindexed_tail_bytes=analysis['unindexed_tail_bytes'],
            attempts=attempts, diagnostics=diagnostics, kernel_candidate_count=len(analysis['reports']))
        output_name = 'evidence-' + uuid.uuid4().hex
        os.mkdir(output_name, 0o700, dir_fd=root_fd)
        output_fd = os.open(output_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        def write(name, value):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=output_fd)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
        write('analysis.json', analysis)
        write('summary.json', summary)
        for report in analysis['reports']:
            write(report['report_id'] + '.json', report)
        write('complete.json', dict(schema_version=1, export_complete=True, capture_id=ident,
            meaning='Export completed, not proof of complete UART evidence'))
        os.fsync(output_fd)
        os.fsync(root_fd)
        return campaign / output_name
    finally:
        for fd in (output_fd, capture_fd, root_fd):
            if fd is not None:
                os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline UART evidence export; stop campaign first')
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--capture-root', required=True, help='Trusted logs_dir/uart directory; metadata paths are ignored')
    args = parser.parse_args(argv)
    try:
        output = export_campaign(args.campaign, args.capture_root)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, 'Evidence export failed: ' + str(error) + '\n')
    print(str(output))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
