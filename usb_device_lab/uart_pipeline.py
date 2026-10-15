"""Complete UART CLI: manifest seeds, existing resume, bounded post-capture evidence."""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import threading
import uuid
from pathlib import Path
import tomllib
from .uart_reports import AttemptWindow, analyze

NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
BATCH_BYTES = 2 * 1024 * 1024
OVERLAP_BYTES = 256 * 1024


def dump(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def select_seeds(config):
    from .model import DeviceConfig
    with config.manifest.open('rb') as f:
        data = f.read(65537)
    if len(data)>65536:
        raise ValueError('Manifest exceeds 64 KiB')
    manifest = tomllib.loads(data.decode('utf-8'))
    if set(manifest) != {'corpus','family'} or type(manifest['corpus']) is not dict or set(manifest['corpus'])-{'format','seed_dir'} or type(manifest['corpus'].get('format')) is not int or manifest['corpus']['format'] != 1:
        raise ValueError('Unsupported corpus manifest')
    rows, known_families, known_profiles = [], set(), set()
    families = manifest['family']
    if type(families) is not list or not 1<=len(families)<=128:
        raise ValueError('Invalid manifest families')
    for family in families:
        if type(family) is not dict or set(family) != {'name','profiles'} or type(family['name']) is not str or NAME.fullmatch(family['name']) is None or family['name'] in known_families:
            raise ValueError('Invalid/duplicate manifest family')
        name = family['name']
        known_families.add(name)
        profiles = family['profiles']
        if type(profiles) is not list or not profiles or len(profiles)>128 or any(type(p) is not str for p in profiles) or len(set(profiles)) != len(profiles):
            raise ValueError('Invalid manifest profiles')
        for profile in profiles:
            if type(profile) is not str or NAME.fullmatch(profile) is None:
                raise ValueError('Invalid manifest profile')
            qualified = name+'/'+profile
            known_profiles.update((profile,qualified))
            rows.append((name,profile,qualified))
    if set(config.families)-known_families or set(config.profiles)-known_profiles:
        raise ValueError('Unknown family/profile selector')
    selected = []
    root = config.seed_dir.resolve()
    for family, profile, qualified in rows:
        if config.families and family not in config.families:
            continue
        if config.profiles and profile not in config.profiles and qualified not in config.profiles:
            continue
        path = config.seed_dir/(family+'-'+profile+'.json')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Seed escapes configured seed root')
        device = DeviceConfig.load(path)
        selected.append(dict(family=family,profile=qualified,path=str(path.resolve()),digest=device.digest))
    if not selected or len(selected)>128:
        raise ValueError('No seeds selected or seed limit exceeded')
    return selected


def prepare_seeds(config, selection):
    from .model import DeviceConfig
    identity=hashlib.sha256(json.dumps(selection,sort_keys=True).encode()).hexdigest()
    root=config.corpus_out/('uart-seeds-'+identity)
    root.mkdir(parents=True,exist_ok=True)
    paths=[]
    for number,row in enumerate(selection):
        device=DeviceConfig.load(row['path'])
        if device.digest!=row['digest']:
            raise ValueError('Selected seed changed before snapshot creation')
        data=device.data
        data['udc_device'],data['udc_driver']=config.udc,config.gadget_udc_driver
        device=DeviceConfig(data)
        payload=device.canonical().encode()
        path=root/('%03d-'%number+device.digest+'.json')
        try:
            with path.open('xb') as f:
                f.write(payload);f.flush();os.fsync(f.fileno())
        except FileExistsError:
            if path.read_bytes()!=payload:
                raise ValueError('Prepared seed snapshot changed')
        paths.append(str(path))
    return paths


def records(path):
    with Path(path).open('rb') as f:
        while True:
            line = f.readline(65537)
            if not line:
                return
            if len(line)>65536:
                raise ValueError('JSONL record exceeds 64 KiB')
            if not line.endswith(b'\n'):
                return
            value = json.loads(line)
            if type(value) is not dict:
                raise ValueError('JSONL object required')
            yield value


def windows(root, ident):
    result = []
    for directory in sorted(root.glob('attempt-*')):
        if re.fullmatch(r'attempt-[0-9]{6}',directory.name) is None:
            raise ValueError('Invalid attempt name')
        path = directory/'window.json'
        if not path.exists():
            continue
        value = json.loads(path.read_text())
        window = AttemptWindow(**value)
        if window.capture_id != ident or window.attempt_id != directory.name:
            raise ValueError('Window identity mismatch')
        result.append(window)
    if len(result)>10000:
        raise ValueError('Window count limit exceeded')
    for previous, current in zip(result,result[1:]):
        if previous.end_ns>current.start_ns:
            raise ValueError('Overlapping windows require the legacy ambiguity analyzer')
    return result


def analyze_capture(config, root, capture, selection):
    ident = capture.name
    ordered = windows(root,ident)
    output = root/('pipeline-'+uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    dump(output/'selection.json',selection)
    database = sqlite3.connect(output/'candidates.sqlite3')
    database.execute('CREATE TABLE reports(epoch INTEGER,start INTEGER,end INTEGER,attempt TEXT,payload TEXT,PRIMARY KEY(epoch,start))')
    database.execute('CREATE TABLE diagnostics(kind TEXT,attempt TEXT,payload TEXT)')
    index, previous_time, previous_epoch, sequence, cursor = 0, 0, 0, 0, 0
    batch, payload, active = [], bytearray(), None
    parts = 0
    def flush(keep):
        nonlocal batch,payload,parts
        if not batch:
            return
        base = batch[0]['offset_start']
        translated = []
        for number,c in enumerate(batch,1):
            item = dict(c,sequence=number,offset_start=c['offset_start']-base,offset_end=c['offset_end']-base)
            translated.append(item)
        result = analyze(bytes(payload),translated,[active],capture_id=ident)
        for report in result['reports']:
            for key in ('offset_start','offset_end','context_offset_start'):
                report[key] += base
            report.update(kind='kernel_report_candidate',batch_boundary_uncertain=True)
            row = database.execute('SELECT end FROM reports WHERE epoch=? AND start=?',(report['connection_epoch'],report['offset_start'])).fetchone()
            if row is None or report['offset_end']>row[0]:
                database.execute('INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?)',
                    (report['connection_epoch'],report['offset_start'],report['offset_end'],active.attempt_id,json.dumps(report,ensure_ascii=False)))
        database.commit()
        part=root/('evidence-'+uuid.uuid4().hex)
        part.mkdir(mode=0o700)
        for number,report in enumerate(result['reports']):
            report.update(report_id='report-%06d'%number,kind='kernel_report_candidate')
        summary=dict(schema_version=1,capture_id=ident,causal=False,completeness='unknown',
            indexed_bytes=len(payload),unindexed_tail_bytes=0,scope='window_batch',byte_offset_base=base,
            attempts=[dict(attempt_id=active.attempt_id,window_available=True,result_available=(root/active.attempt_id/'result.json').exists(),
                candidate_reports=[r['report_id'] for r in result['reports']])],
            diagnostics=[dict(kind='batch_boundary',summary='Bounded batch with overlap; report boundary/context may be incomplete')],
            kernel_candidate_count=len(result['reports']))
        dump(part/'analysis.json',result)
        dump(part/'summary.json',summary)
        dump(part/'complete.json',dict(schema_version=1,capture_id=ident,export_complete=True))
        with (output/'exports.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(directory=part.name,attempt=active.attempt_id,byte_offset_base=base))+'\n')
        parts += 1
        if keep:
            retained, size = [], 0
            for c in reversed(batch):
                if size+c['byte_count']>OVERLAP_BYTES:
                    break
                retained.insert(0,c);size+=c['byte_count']
            batch = retained
            payload = payload[-size:] if size else bytearray()
        else:
            batch,payload = [],bytearray()
    try:
        with (capture/'uart.raw').open('rb') as raw:
            for c in records(capture/'chunks.jsonl'):
                keys=('sequence','offset_start','offset_end','byte_count','monotonic_ns','connection_epoch')
                if any(type(c.get(k)) is not int for k in keys) or c['sequence'] != sequence+1 or c['offset_start'] != index or not 0<c['byte_count']<=65536 or c['offset_end'] != index+c['byte_count'] or c['monotonic_ns']<previous_time or c['connection_epoch']<max(1,previous_epoch):
                    raise ValueError('Inconsistent capture index')
                sequence,index,previous_time,previous_epoch=c['sequence'],c['offset_end'],c['monotonic_ns'],c['connection_epoch']
                while cursor<len(ordered) and c['monotonic_ns']>=ordered[cursor].end_ns:
                    flush(False);cursor+=1;active=None
                if cursor>=len(ordered) or c['monotonic_ns']<ordered[cursor].start_ns:
                    continue
                window = ordered[cursor]
                if active is None:
                    active = window
                if batch and batch[-1]['connection_epoch'] != c['connection_epoch']:
                    flush(False)
                if batch and (len(payload)+c['byte_count']>BATCH_BYTES or len(batch)>=4096):
                    flush(True)
                raw.seek(c['offset_start'])
                data = raw.read(c['byte_count'])
                if len(data) != c['byte_count']:
                    raise ValueError('Raw capture shorter than its index')
                batch.append(c);payload.extend(data)
            flush(False)
            raw_size = os.fstat(raw.fileno()).st_size
            if raw_size<index:
                raise ValueError('Raw capture shorter than indexed prefix')
        for event in records(capture/'events.jsonl'):
            if event.get('kind') in ('open_failed','disconnected','failed'):
                database.execute('INSERT INTO diagnostics VALUES (?,?,?)',('uart_capture',None,json.dumps(event,ensure_ascii=False)))
        for directory in sorted(root.glob('attempt-*')):
            path = directory/'result.json'
            if not path.exists():
                database.execute('INSERT INTO diagnostics VALUES (?,?,?)',('attempt_incomplete',directory.name,'{}'))
                continue
            value = json.loads(path.read_text())
            if value.get('infrastructure_error'):
                database.execute('INSERT INTO diagnostics VALUES (?,?,?)',('campaign_infrastructure',directory.name,json.dumps(value['infrastructure_error'])))
            if value.get('executor_returncode') not in (None,0,-15,-9):
                database.execute('INSERT INTO diagnostics VALUES (?,?,?)',('executor_exit',directory.name,json.dumps(value['executor_returncode'])))
        database.commit()
        candidate_root = config.corpus_out/'uart-candidates'
        candidate_root.mkdir(parents=True,exist_ok=True)
        count = 0
        with (output/'reports.jsonl').open('x',encoding='utf-8') as file:
            for epoch,start,end,attempt,text in database.execute('SELECT * FROM reports ORDER BY start'):
                report = json.loads(text)
                file.write(text+'\n');count+=1
                source = root/attempt/'config.json'
                data = source.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                destination = candidate_root/(digest+'.json')
                try:
                    with destination.open('xb') as f:
                        f.write(data);f.flush();os.fsync(f.fileno())
                except FileExistsError:
                    if destination.read_bytes()!=data:
                        raise ValueError('Candidate archive digest/content mismatch')
                report_name = 'report-%06d.json'%count
                dump(output/report_name,report)
            file.flush();os.fsync(file.fileno())
        summary = dict(schema_version=2,capture_id=ident,analysis_complete=True,causal=False,completeness='unknown',
            indexed_bytes=index,unindexed_tail_bytes=max(0,raw_size-index),attempts=len(ordered),batches=parts,
            kernel_candidate_count=count,candidate_directory=str(candidate_root),
            diagnostics=database.execute('SELECT COUNT(*) FROM diagnostics').fetchone()[0],
            format='streamed JSONL and SQLite; not legacy HTML schema')
        dump(output/'pipeline-summary.json',summary)
        dump(output/'complete.json',dict(schema_version=2,export_complete=True,capture_id=ident,causal=False))
        return dict(directory=str(output),summary=summary)
    except Exception as error:
        dump(output/'analysis-error.json',dict(analysis_complete=False,error=str(error),capture_id=ident,causal=False))
        raise
    finally:
        database.close()


def run_complete(config, *, resume=None, stop=None, preflight=True, command_factory=None, lock_root=Path('/run/lock/usb-device-lab')):
    from .uart_campaign import run_campaign
    from .uart_receiver import UARTReceiver
    from .uart_preflight import inspect
    selection = select_seeds(config)
    seeds = [row['path'] for row in selection]
    if preflight:
        report = inspect(config,seeds,lock_root=lock_root)
        if report['metadata_checks_passed'] is not True:
            raise RuntimeError('Metadata preflight failed: '+json.dumps(report))
    seeds=prepare_seeds(config,selection)
    receivers = []
    def receiver_factory(settings,directory):
        receiver=UARTReceiver(settings,directory)
        receivers.append((receiver,Path(directory)))
        return receiver
    result,error=None,None
    try:
        result=run_campaign(config,seeds,resume=resume,stop=stop,receiver_factory=receiver_factory,
                            command_factory=command_factory,lock_root=lock_root)
    except Exception as caught:
        error=caught
    if receivers:
        receiver,capture=receivers[-1]
        if receiver.status()['state'] not in ('stopped','failed'):
            raise RuntimeError('Capture did not stop; automatic analysis refused')
        root=config.results_dir/capture.name
        dump(root/'selection.json',selection)
        analyzed=analyze_capture(config,root,capture,selection)
        if result is not None:result['analysis']=analyzed
    if error is not None:raise error
    return result


def main(argv=None):
    from .uart_config import load
    p=argparse.ArgumentParser(description='Complete manifest-selected UART CLI with post-capture analysis')
    p.add_argument('--config',required=True)
    p.add_argument('--resume')
    args=p.parse_args(argv)
    stop=threading.Event()
    previous={sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        print(json.dumps(run_complete(load(args.config),resume=args.resume,stop=stop),ensure_ascii=False))
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    return 0

if __name__=='__main__':raise SystemExit(main())
