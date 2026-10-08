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


def run_session(config, stop, publish):
    from .lab import prepare_seeds, gather_checks, HostOrchestrator, agent_argv
    from .model import DeviceConfig
    from .mutator import Mutator
    from .runner import execute, run_summary, FATAL
    from .storage import Store
    config.results_dir.mkdir(parents=True, exist_ok=True)
    completed = 0
    with open(config.results_dir / 'campaign.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stop.is_set():
            return {'runs': 0, 'fatal': False}
        checks = gather_checks(config)
        if not checks['ready']:
            raise RuntimeError('Стенд не готов; выполните lab check')
        seeds = prepare_seeds(config)
        store = Store(config.results_dir)
        try:
            store.recover()
            for path in seeds:
                store.seed(DeviceConfig.load(path))
            rng = random.Random(config.seed)
            mutator = Mutator(config.seed)
            with HostOrchestrator(config):
                for iteration in range(config.iterations):
                    if stop.is_set():
                        break
                    parent = DeviceConfig.load(rng.choice(store.corpus()))
                    device, mutation = (parent, []) if iteration == 0 else mutator.mutate(parent)
                    ident = store.begin(device, {'parent': parent.digest, 'mutation': mutation,
                                                'prng_seed': config.seed, 'iteration': iteration})
                    directory = store.root / 'runs' / ident
                    gadget = [sys.executable, '-u', '-m', 'usb_device_lab.device', str(directory / 'config.json')]
                    result = execute(directory, agent_argv(config), gadget, config.seconds)
                    result['iteration'] = iteration
                    new = store.finish(ident, device, result)
                    completed += 1
                    publish(run_summary(ident, result, new))
                    if any(error['kind'] in FATAL for error in result['errors']):
                        return {'runs': completed, 'fatal': True}
            return {'runs': completed, 'fatal': False}
        finally:
            store.close()


class CampaignSupervisor:
    def __init__(self, config, backend=run_session):
        self.config = config
        self.backend = backend
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last = None
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
            self._status = {'state': 'starting', 'session': uuid.uuid4().hex, 'runs': 0, 'error': None}
            self._thread = threading.Thread(target=self._work, args=(config,), daemon=False)
            self._thread.start()
            return dict(self._status)

    def _publish(self, summary):
        with self._lock:
            self._status['runs'] += 1
            self._status['last_run'] = summary['run']
            if not self._stop.is_set():
                self._status['state'] = 'running'

    def _work(self, config):
        try:
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
            return self.start(**self._last)

    def wait(self, timeout=None):
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()
