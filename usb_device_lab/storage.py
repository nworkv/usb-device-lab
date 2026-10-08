"""SQLite index and per-run diagnostic artifacts.

Legacy database statuses remain compatible with the existing dashboard.
The input snapshot is executor configuration, not a capture of wire traffic.
Each file is replaced atomically; files and SQLite are not one transaction.
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from .errors import kernel_events, make_verdict


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temp, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


class Store:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'runs.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,started REAL,finished REAL,digest TEXT,status TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS coverage(namespace TEXT,pc TEXT,PRIMARY KEY(namespace,pc));
        CREATE TABLE IF NOT EXISTS corpus(digest TEXT PRIMARY KEY,added REAL);
        CREATE TABLE IF NOT EXISTS errors(signature TEXT PRIMARY KEY,kind TEXT,first_run TEXT,last_run TEXT,occurrences INTEGER);
        """)
        self.db.commit()

    def seed(self, config):
        atomic_write(self.root / 'corpus' / (config.digest + '.json'), config.canonical())
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO corpus VALUES (?,?)', (config.digest, time.time()))

    def begin(self, config, metadata=None):
        ident = uuid.uuid4().hex
        started = time.time()
        directory = self.root / 'runs' / ident
        canonical = config.canonical()
        atomic_write(directory / 'config.json', canonical)
        atomic_write(directory / 'input.executed.json', canonical)
        write_json(directory / 'metadata.json', metadata or {})
        write_json(directory / 'run.json', {
            'schema_version': 1,
            'run_id': ident,
            'started': started,
            'finished': None,
            'status': 'running',
            'outcome': None,
            'input_digest': config.digest,
            'input_kind': 'executor_configuration',
        })
        with self.db:
            self.db.execute('INSERT INTO runs VALUES (?,?,NULL,?,?,NULL)',
                            (ident, started, config.digest, 'running'))
        return ident

    def finish(self, ident, config, result):
        namespace = result.get('namespace', '')
        pcs = {str(value) for value in result.get('pcs', [])}
        if pcs and not namespace:
            raise ValueError('coverage namespace required')
        directory = self.root / 'runs' / ident
        new = 0
        with self.db:
            row = self.db.execute('SELECT digest,status,started FROM runs WHERE id=?', (ident,)).fetchone()
            if not row or row['status'] != 'running' or row['digest'] != config.digest:
                raise ValueError('run state/config mismatch')
            accepted = bool(result.get('coverage_valid') and pcs and namespace
                            and not result.get('saturated'))
            result['coverage_valid'] = accepted
            if accepted:
                for pc in pcs:
                    new += self.db.execute('INSERT OR IGNORE INTO coverage VALUES (?,?)', (namespace, pc)).rowcount
                if new:
                    atomic_write(self.root / 'corpus' / (config.digest + '.json'), config.canonical())
                    self.db.execute('INSERT OR IGNORE INTO corpus VALUES (?,?)', (config.digest, time.time()))
            result['new_pcs'] = new
            result['corpus_added'] = bool(new)
            result['kernel_events'] = kernel_events(result.get('kernel_log', ''))
            verdict = make_verdict(result)
            if verdict['outcome'] == 'coverage_observed':
                verdict['outcome'] = 'new_coverage' if new else 'no_change'
            verdict['new_pcs'] = new
            verdict['observed_pc_count'] = len(pcs)
            verdict['namespace'] = namespace
            result['verdict'] = verdict
            for error in result.get('errors', []):
                signature = hashlib.sha256((error['kind'] + ':' + error['summary']).encode()).hexdigest()
                self.db.execute("""INSERT INTO errors VALUES (?,?,?,?,1) ON CONFLICT(signature)
                    DO UPDATE SET last_run=excluded.last_run,occurrences=errors.occurrences+1""",
                    (signature, error['kind'], ident, ident))
            finished = time.time()
            status = 'error' if result.get('errors') or result['kernel_events'] else 'ok'
            write_json(directory / 'coverage.json', {
                'schema_version': 1,
                'namespace': namespace,
                'coverage_valid': accepted,
                'saturated': bool(result.get('saturated')),
                'pcs': sorted(pcs),
                'observed_pc_count': len(pcs),
                'new_pcs': new,
                'corpus_added': bool(new),
            })
            write_json(directory / 'kernel_events.json', result['kernel_events'])
            atomic_write(directory / 'kmsg.delta.log', result.get('kernel_log', ''))
            write_json(directory / 'verdict.json', verdict)
            write_json(directory / 'result.json', result)
            write_json(directory / 'run.json', {
                'schema_version': 1,
                'run_id': ident,
                'started': row['started'],
                'finished': finished,
                'status': status,
                'outcome': verdict['outcome'],
                'input_digest': config.digest,
                'input_kind': 'executor_configuration',
                'artifacts': [
                    'config.json', 'input.executed.json', 'metadata.json',
                    'coverage.json', 'kernel_events.json', 'kmsg.delta.log',
                    'verdict.json', 'result.json',
                ],
            })
            self.db.execute('UPDATE runs SET finished=?,status=?,result=? WHERE id=?',
                            (finished, status, json.dumps(result), ident))
        return new

    def corpus(self):
        return [self.root / 'corpus' / (row[0] + '.json')
                for row in self.db.execute('SELECT digest FROM corpus ORDER BY digest')]

    def recover(self):
        with self.db:
            rows = self.db.execute("SELECT id,started,digest FROM runs WHERE status='running'").fetchall()
            for row in rows:
                finished = time.time()
                verdict = {
                    'schema_version': 1,
                    'outcome': 'interrupted',
                    'kernel_evidence_detected': None,
                    'confirmation': 'unknown',
                    'telemetry_complete': False,
                    'reason': 'unfinished run recovered; cause not established',
                }
                directory = self.root / 'runs' / row['id']
                write_json(directory / 'verdict.json', verdict)
                write_json(directory / 'run.json', {
                    'schema_version': 1, 'run_id': row['id'],
                    'started': row['started'], 'finished': finished,
                    'status': 'interrupted', 'outcome': 'interrupted',
                    'input_digest': row['digest'],
                })
                self.db.execute("UPDATE runs SET status='interrupted',finished=? WHERE id=?",
                                (finished, row['id']))

    def close(self):
        self.db.close()
