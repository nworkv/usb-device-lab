"""Read-only views of selected seeds and the SQLite-indexed campaign corpus."""
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from .lab import select_seeds
from .model import DeviceConfig
from .topology import validate_topology

MAX_CONFIG = 262144


class CorpusBrowser:
    def __init__(self, config):
        self.config = config

    def candidates(self, source, offset=0):
        if source not in ('seeds', 'learned') or type(offset) is not int or not 0 <= offset <= 10000000:
            raise ValueError('Invalid source or offset')
        if source == 'seeds':
            paths = sorted(select_seeds(self.config), key=lambda path: path.name)
            return [(hashlib.sha256(path.name.encode()).hexdigest(), path.name) for path in paths[offset:offset+201]]
        database = Path(self.config.results_dir).resolve() / 'runs.sqlite3'
        if not database.exists():
            return []
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            rows = db.execute('SELECT digest FROM corpus ORDER BY digest LIMIT 201 OFFSET ?', (offset,)).fetchall()
        if any(type(row[0]) is not str or re.fullmatch('[0-9a-f]{64}', row[0]) is None for row in rows):
            raise ValueError('Invalid corpus index')
        return [(row[0], row[0] + '.json') for row in rows]

    def filename(self, source, ident):
        if type(ident) is not str or re.fullmatch('[0-9a-f]{64}', ident) is None:
            raise ValueError('Invalid corpus identifier')
        if source == 'seeds':
            for path in select_seeds(self.config):
                if hashlib.sha256(path.name.encode()).hexdigest() == ident:
                    return path.name
        elif source == 'learned':
            database = Path(self.config.results_dir).resolve() / 'runs.sqlite3'
            if database.exists():
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                    if db.execute('SELECT 1 FROM corpus WHERE digest=?', (ident,)).fetchone():
                        return ident + '.json'
        else:
            raise ValueError('Invalid corpus source')
        raise FileNotFoundError('Corpus entry not indexed')

    def read(self, source, name):
        if not name.endswith('.json') or '/' in name or chr(92) in name or name in ('.', '..'):
            raise ValueError('Invalid corpus filename')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        root = Path(self.config.seed_dir if source == 'seeds' else self.config.results_dir).resolve()
        directory = os.open(root, flags)
        try:
            if source == 'learned':
                child = os.open('corpus', flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG:
                    raise ValueError('Corpus entry is not a bounded regular file')
                raw = os.pread(descriptor, MAX_CONFIG + 1, 0)
                if len(raw) > MAX_CONFIG:
                    raise ValueError('Corpus entry too large')
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)
        def constant(value):
            raise ValueError('Non-finite JSON value')
        def number(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError('Non-finite JSON number')
            return parsed
        data = json.loads(raw.decode('utf-8'), parse_constant=constant, parse_float=number)
        device = DeviceConfig(data)
        if source == 'learned' and device.digest != name[:-5]:
            raise ValueError('Corpus digest mismatch')
        return device, len(raw)

    def card(self, source, ident, name, detail=False):
        device, size = self.read(source, name)
        data = device.data
        metadata = data.get('metadata') or {}
        if type(metadata) is not dict:
            raise ValueError('Invalid corpus metadata')
        family = metadata.get('family', 'unknown')
        profile = metadata.get('profile', 'unknown')
        if type(family) is not str or type(profile) is not str:
            raise ValueError('Invalid corpus selectors')
        family, profile = family[:127], profile[:127]
        topology = validate_topology(data)
        interfaces = [interface for configuration in topology.values() for interface in configuration['interfaces']]
        alternates = [alternate for interface in interfaces for alternate in interface['alternates']]
        result = {'id': ident, 'source': source, 'name': name, 'valid': True, 'digest': device.digest,
                  'family': family, 'profile': profile, 'size': size, 'speed': data.get('speed', 2),
                  'udc_driver': data['udc_driver'], 'udc_device': data['udc_device'],
                  'descriptor_count': len(data['descriptors']), 'configuration_count': len(topology),
                  'interface_count': len(interfaces), 'alternate_count': len(alternates),
                  'endpoint_count': sum(len(alt.get('endpoints', [])) for alt in alternates),
                  'description': str(metadata.get('description') or f'Семейство: {family}; профиль: {profile}')[:1024]}
        if detail:
            result['config'] = data
        return result

    def list(self, source='seeds', offset=0, family='', profile=''):
        if any(type(value) is not str or len(value) > 127 for value in (family, profile)):
            raise ValueError('Invalid corpus filter')
        candidates = self.candidates(source, offset)
        items, scanned = [], 0
        for ident, name in candidates[:200]:
            scanned += 1
            try:
                card = self.card(source, ident, name)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                card = {'id': ident, 'source': source, 'name': name, 'valid': False,
                        'family': 'unknown', 'profile': 'unknown', 'error': 'Конфигурация недоступна или некорректна'}
            if family and card['family'] != family or profile and card['profile'] != profile:
                continue
            items.append(card)
            if len(items) == 50:
                break
        return {'items': items, 'scanned': scanned, 'next_offset': offset + scanned,
                'more': scanned < len(candidates)}

    def detail(self, source, ident):
        return self.card(source, ident, self.filename(source, ident), detail=True)
