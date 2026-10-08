"""Bounded SQLite event journal with stable cursors and explicit retention gaps."""
import json
import sqlite3
import threading
import time


class EventJournal:
    def __init__(self, path, capacity=500):
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError('Invalid journal capacity')
        self.capacity = capacity
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        try:
            self._db.execute('PRAGMA synchronous=FULL')
            if self._db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('Unsupported journal schema')
            with self._db:
                self._db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, payload TEXT NOT NULL)')
                self._db.execute('PRAGMA user_version=1')
        except Exception:
            self._db.close()
            raise

    def append(self, status):
        payload = json.dumps(status, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 16384:
            raise ValueError('Event payload too large')
        with self._lock:
            if self._closed:
                raise RuntimeError('Event journal is closed')
            with self._db:
                cursor = self._db.execute('INSERT INTO events(timestamp,payload) VALUES (?,?)', (time.time(), payload))
                sequence = cursor.lastrowid
                self._db.execute('DELETE FROM events WHERE seq <= ?', (sequence - self.capacity,))
            return sequence

    def read(self, after=0, limit=100):
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid event cursor or limit')
        with self._lock:
            if self._closed:
                raise RuntimeError('Event journal is closed')
            oldest, latest = self._db.execute('SELECT COALESCE(MIN(seq),0),COALESCE(MAX(seq),0) FROM events').fetchone()
            if after > latest:
                raise ValueError('Cursor is newer than this journal')
            rows = self._db.execute('SELECT seq,timestamp,payload FROM events WHERE seq > ? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
        events = [{'seq': seq, 'timestamp': timestamp, 'status': json.loads(payload)} for seq, timestamp, payload in rows]
        next_cursor = events[-1]['seq'] if events else after
        return {'events': events, 'next': next_cursor, 'oldest': oldest, 'latest': latest,
                'gap': bool(oldest and after < oldest - 1), 'more': next_cursor < latest}

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True
