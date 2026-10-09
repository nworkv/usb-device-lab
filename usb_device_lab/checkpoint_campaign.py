"""Dashboard campaign checkpoints; mismatched DB/checkpoint boundaries fail closed."""
import fcntl
import hashlib
import json
import random
import sys
import uuid
from pathlib import Path
from .checkpoint import capture_checkpoint, load_checkpoint, parameters, restore_generators, save_checkpoint
from .corpus_browser import CorpusBrowser
from .lab import prepare_seeds, gather_checks, HostOrchestrator, agent_argv
from .model import DeviceConfig
from .mutator import Mutator
from .runner import execute, run_summary, FATAL
from .storage import Store


def lab_identity(config):
    values = {key: str(value) for key, value in vars(config).items()
              if key not in {'iterations', 'seconds', 'seed', 'families', 'profiles'}}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def engine_identity():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('checkpoint_campaign.py', 'checkpoint.py', 'control.py', 'corpus_browser.py',
                 'mutator.py', 'model.py', 'topology.py', 'runner.py', 'storage.py', 'lab.py',
                 'device.py', 'raw_io.py', 'protocol.py', 'errors.py', 'host_agent.py', 'host_logs.py'):
        digest.update(name.encode() + bytes([0]) + (root / name).read_bytes())
    return digest.hexdigest()


def verified_corpus(store, config):
    paths = store.corpus()
    if not 1 <= len(paths) <= 4096:
        raise ValueError('Checkpoint corpus must contain 1..4096 entries')
    browser = CorpusBrowser(config)
    digests = []
    for path in paths:
        device, size = browser.read('learned', Path(path).name)
        if device.digest != Path(path).stem:
            raise ValueError('Corpus digest mismatch')
        digests.append(device.digest)
    if digests != sorted(set(digests)):
        raise ValueError('Corpus order or duplicates invalid')
    return digests


def verify_boundary(store, value):
    if store.db.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
        raise RuntimeError('Unfinished run exists; inspect artifacts and explicitly start a new campaign')
    rows = store.db.execute('SELECT a.iteration,a.run_id,r.status,r.result FROM campaign_attempts a '
                            'LEFT JOIN runs r ON r.id=a.run_id WHERE a.campaign_id=? ORDER BY a.iteration',
                            (value['campaign_id'],))
    count, last, last_result = 0, None, None
    for iteration, ident, status, result in rows:
        if iteration != count or status not in ('ok', 'error') or result is None:
            raise RuntimeError('Campaign attempt tracking is inconsistent')
        count += 1
        last, last_result = ident, json.loads(result)
    if count != value['next_iteration'] or last != value['last_run']:
        raise RuntimeError('DB/checkpoint gap detected; no automatic retry or repair')
    if last_result is not None:
        if type(last_result) is not dict or any(error.get('kind') in FATAL for error in last_result.get('errors', [])):
            raise RuntimeError('Previous attempt failed fatally; repair the lab and start a new campaign')


def run_checkpoint_session(config, stop, publish, resume=False, *, store_cls=Store,
                           execute_fn=execute, checks_fn=gather_checks, prepare_fn=prepare_seeds,
                           orchestrator_cls=HostOrchestrator, agent_fn=agent_argv,
                           identity_fn=lab_identity, engine_fn=engine_identity, corpus_fn=verified_corpus):
    root = Path(config.results_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'campaign-checkpoint.json'
    campaign_parameters = parameters({key: getattr(config, key) for key in
                                     ('iterations', 'seconds', 'seed', 'families', 'profiles')})
    identity, engine = identity_fn(config), engine_fn()
    completed = 0
    with open(root / 'campaign.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stop.is_set():
            return {'runs': 0, 'fatal': False}
        if resume and (not path.is_file() or not (root / 'runs.sqlite3').is_file()):
            raise RuntimeError('Checkpoint or campaign database missing')
        store = store_cls(root)
        try:
            if resume:
                digests = corpus_fn(store, config)
                value = load_checkpoint(path, expected_identity=identity, expected_engine_id=engine,
                                        expected_parameters=campaign_parameters, expected_corpus=digests)
                verify_boundary(store, value)
                first, last, campaign = value['next_iteration'], value['last_run'], value['campaign_id']
                selection, mutation_rng = restore_generators(value)
                mutator = Mutator(0)
                mutator.random = mutation_rng
                if first == config.iterations:
                    raise RuntimeError('Campaign budget is already completed')
            if not checks_fn(config)['ready']:
                raise RuntimeError('Lab checks failed; campaign not started')
            if not resume:
                store.recover()
                for seed_path in prepare_fn(config):
                    store.seed(DeviceConfig.load(seed_path))
                digests = corpus_fn(store, config)
                with store.db:
                    store.db.execute('CREATE TABLE IF NOT EXISTS campaign_attempts '
                                     '(campaign_id TEXT NOT NULL,iteration INTEGER NOT NULL,run_id TEXT NOT NULL UNIQUE,'
                                     'PRIMARY KEY(campaign_id,iteration))')
                first, last, campaign = 0, None, uuid.uuid4().hex
                selection, mutator = random.Random(config.seed), Mutator(config.seed)
                save_checkpoint(path, capture_checkpoint(identity, engine, campaign, campaign_parameters,
                                                         digests, first, last, selection, mutator.random))
            publish({'checkpoint_progress': True, 'next_iteration': first, 'run': last})
            if stop.is_set():
                return {'runs': 0, 'fatal': False}
            with orchestrator_cls(config):
                for iteration in range(first, config.iterations):
                    if stop.is_set():
                        break
                    selected = Path(selection.choice(store.corpus()))
                    parent, size = CorpusBrowser(config).read('learned', selected.name)
                    device, mutation = (parent, []) if iteration == 0 else mutator.mutate(parent)
                    ident = store.begin(device, {'campaign_id': campaign, 'parent': parent.digest,
                                                'mutation': mutation, 'prng_seed': config.seed, 'iteration': iteration})
                    with store.db:
                        store.db.execute('INSERT INTO campaign_attempts VALUES (?,?,?)', (campaign, iteration, ident))
                    directory = store.root / 'runs' / ident
                    gadget = [sys.executable, '-u', '-m', 'usb_device_lab.device', str(directory / 'config.json')]
                    result = execute_fn(directory, agent_fn(config), gadget, config.seconds)
                    result['iteration'] = iteration
                    result['campaign_id'] = campaign
                    new = store.finish(ident, device, result)
                    digests = corpus_fn(store, config)
                    save_checkpoint(path, capture_checkpoint(identity, engine, campaign, campaign_parameters,
                                                             digests, iteration + 1, ident, selection, mutator.random))
                    completed += 1
                    summary = run_summary(ident, result, new)
                    summary['iteration'] = iteration
                    publish(summary)
                    if any(error['kind'] in FATAL for error in result['errors']):
                        return {'runs': completed, 'fatal': True}
            return {'runs': completed, 'fatal': False}
        finally:
            store.close()
