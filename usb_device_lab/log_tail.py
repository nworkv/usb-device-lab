"""Bounded reads of allowlisted run logs without following directory/file symlinks."""
import os
import errno
import re
import stat
from pathlib import Path

SOURCES = {'executor': 'executor.log', 'agent': 'agent.log', 'kernel': 'kmsg.delta.log'}
MAX_BYTES = 65536


def open_log(directory, run, source):
    if type(run) is not str or re.fullmatch(r'[0-9a-f]{32}', run) is None or type(source) is not str or source not in SOURCES:
        raise ValueError('Invalid run or log source')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(Path(directory).resolve(), flags)
    try:
        for component in ('runs', run):
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        result = os.open(SOURCES[source], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(result).st_mode):
                raise ValueError('Log is not a regular file')
        except Exception:
            os.close(result)
            raise
        return result
    finally:
        os.close(descriptor)


def list_logs(directory, run):
    result = []
    for source, name in SOURCES.items():
        try:
            descriptor = open_log(directory, run, source)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR, errno.EACCES):
                raise
            result.append({'source': source, 'name': name, 'available': False})
            continue
        try:
            info = os.fstat(descriptor)
            result.append({'source': source, 'name': name, 'available': True, 'size': info.st_size})
        finally:
            os.close(descriptor)
    return result


def read_tail(directory, run, source, lines=200):
    if type(lines) is not int or not 1 <= lines <= 1000:
        raise ValueError('Lines must be 1..1000')
    descriptor = open_log(directory, run, source)
    try:
        before = os.fstat(descriptor)
        offset = max(0, before.st_size - MAX_BYTES)
        data = os.pread(descriptor, MAX_BYTES, offset)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    pieces = data.splitlines(keepends=True)
    selected = pieces[-lines:]
    text = b''.join(selected).decode('utf-8', errors='replace')
    return {'run': run, 'source': source, 'name': SOURCES[source], 'text': text,
            'size': before.st_size, 'bytes_read': len(data), 'lines_returned': len(selected),
            'truncated': bool(offset or len(pieces) > lines), 'partial_first_line_possible': bool(offset and len(pieces) <= lines),
            'changed_during_read': before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns,
            'saved_kernel_log': source == 'kernel'}
