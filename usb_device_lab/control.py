"""Local campaign supervisor. Stop is cooperative at iteration boundaries."""
import fcntl
import math
import random
import sys
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from .lab import select_seeds


def run_session(config, stop, publish, resume=False):
    from .checkpoint_campaign import run_checkpoint_session
    return run_checkpoint_session(config, stop, publish, resume=resume)


class CampaignSupervisor:
    def __init__(self, config, backend=run_session):
        self.config = config
        self.backend = backend
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last = None
        self._resume_requested = False
        self._status = {'state': 'idle', 'session': None, 'runs': 0, 'error': None}

    def status(self):
        with self._lock:
            return dict(self._status)

    def start(self, iterations=None, seconds=None, seed=None, families=None, profiles=None):
        iterations = self.config.iterations if iterations is None else iterations
        seconds = self.config.seconds if seconds is None else seconds
        seed = self.config.seed if seed is None else seed
        if type(iterations) is not int or not 1 <= iterations <= 1000000:
            raise ValueError('Число итераций должно быть от 1 до 1000000')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 300:
            raise ValueError('Окно попытки должно быть больше 0 и не больше 300 секунд')
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError('Seed должен быть целым числом от 0 до 2**63-1')
        for names in (families, profiles):
            if names is not None and (type(names) not in (list, tuple) or any(type(name) is not str for name in names)):
                raise ValueError('Семейства и профили должны быть списками строк')
        config = replace(self.config, iterations=iterations, seconds=seconds, seed=seed,
                         families=self.config.families if families is None else tuple(families),
                         profiles=self.config.profiles if profiles is None else tuple(profiles))
        select_seeds(config)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError('Кампания уже активна')
            self._last = dict(iterations=iterations, seconds=seconds, seed=seed,
                              families=config.families, profiles=config.profiles)
            self._stop = threading.Event()
            self._resume_requested = False
            self._status = {'state': 'starting', 'session': uuid.uuid4().hex, 'runs': 0, 'error': None}
            self._thread = threading.Thread(target=self._work, args=(config,), daemon=False)
            self._thread.start()
            return dict(self._status)

    def _publish(self, summary):
        with self._lock:
            if summary.get('checkpoint_progress'):
                self._status['runs'] = summary['next_iteration']
                if summary.get('run'):
                    self._status['last_run'] = summary['run']
                return
            self._status['runs'] = summary.get('iteration', self._status['runs']) + 1
            self._status['last_run'] = summary['run']
            if not self._stop.is_set():
                self._status['state'] = 'running'

    def _work(self, config):
        try:
            with self._lock:
                resume = self._resume_requested
            if self.backend is run_session:
                result = self.backend(config, self._stop, self._publish, resume=resume)
            else:
                result = self.backend(config, self._stop, self._publish)
            with self._lock:
                self._status['state'] = 'failed' if result['fatal'] else 'stopped' if self._stop.is_set() else 'completed'
                if result['fatal']:
                    self._status['error'] = 'Кампания остановлена из-за критической ошибки; проверьте артефакты'
        except Exception as error:
            with self._lock:
                self._status['state'] = 'failed'
                self._status['error'] = str(error)[:4096]

    def stop(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._stop.set()
                self._status['state'] = 'stopping'
            return dict(self._status)

    def resume(self):
        with self._lock:
            if self._last is None:
                raise RuntimeError('Нет предыдущей кампании')
            status = self.start(**self._last)
            self._resume_requested = True
            return status

    def wait(self, timeout=None):
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()
