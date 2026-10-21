"""Sequential corpus execution; group errors are recorded without aborting the batch."""
import argparse
from dataclasses import replace
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import stat
import tempfile
import threading
import uuid
from .corpus_selection import catalogue
from .uart_pipeline import select_seeds
from .dual_pipeline import run_complete


def make_plan(config):
    families = catalogue(config)['families']
    unknown = set(config.families)-{row['name'] for row in families}
    if unknown:raise ValueError('Unknown batch families: '+', '.join(sorted(unknown)))
    groups, total = [], 0
    for family in families:
        name = family['name']
        if config.families and name not in config.families:continue
        group_config = replace(config,families=(name,))
        try:selection = select_seeds(group_config)
        except ValueError as error:
            if config.profiles and str(error)=='No seeds selected or seed limit exceeded':continue
            raise
        if not 1<=len(selection)<=128:raise ValueError('Batch groups require 1..128 seeds')
        total += len(selection)
        if total>4096:raise ValueError('Batch exceeds 4096 planned attempts')
        groups.append(dict(family=name,selection=selection))
    if not groups:raise ValueError('Empty batch selection')
    return groups


def write_snapshot(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.batch-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def analysis_candidates(result):
    """Unknown/incomplete analysis must not be treated as zero candidates."""
    campaign = result.get('campaign_id')
    if type(campaign) is not str or re.fullmatch(r'[0-9a-f]{32}',campaign) is None:
        raise RuntimeError('Group analysis requires a valid campaign identity')
    analysis = result.get('analysis')
    summary = analysis.get('summary') if type(analysis) is dict else None
    if type(summary) is not dict or summary.get('analysis_complete') is not True:
        raise RuntimeError('Group analysis is missing or incomplete; next group refused')
    if summary.get('capture_id') != campaign:
        raise RuntimeError('Group analysis capture does not match campaign; next group refused')
    count = summary.get('kernel_candidate_count')
    if type(count) is not int or count<0:
        raise RuntimeError('Group analysis candidate count is missing or invalid; next group refused')
    return count


def run_batch(config, *, stop=None, skip=None, pipeline=None, on_progress=None, expected_plan=None):
    groups = make_plan(config)
    if expected_plan is not None and groups != expected_plan:
        raise ValueError('Batch plan changed before execution; no USB launch')
    stop = threading.Event() if stop is None else stop
    skip = threading.Event() if skip is None else skip
    pipeline = run_complete if pipeline is None else pipeline
    progress = dict(schema_version=1,state='planned',batch_id=None,directory=None,
        planned_attempts=sum(len(group['selection']) for group in groups),completed_groups=0,
        failed_groups=0,processed_groups=0,kernel_candidate_count=0,review_required=False,
        groups=[],causal=False,hardware_acceptance=False,error=None)
    if stop.is_set():
        progress['state']='stopped'
        return progress
    parent = config.results_dir/'batches'
    parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid():raise ValueError('Unsafe batch directory')
    fd = os.open(parent/'batch.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():raise ValueError('Unsafe batch lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ident = uuid.uuid4().hex
        root = parent/ident
        root.mkdir(mode=0o700)
        progress.update(batch_id=ident,directory=str(root))
        plan_digest = hashlib.sha256(json.dumps(groups,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        write_snapshot(root/'plan.json',dict(schema_version=1,plan_sha256=plan_digest,groups=groups,
            mode=config.telemetry_mode,udc=config.udc,seconds=config.seconds,
            pre_run_quiet_seconds=config.pre_run_quiet_seconds,post_run_capture_seconds=config.post_run_capture_seconds))
        def publish():
            write_snapshot(root/'progress.json',progress)
            if on_progress:on_progress(json.loads(json.dumps(progress)))
        try:
            publish()
            for index, group in enumerate(groups):
                if stop.is_set():progress['state']='stopped';break
                row = dict(index=index,family=group['family'],planned=len(group['selection']),
                    state='running',campaign_id=None,attempts_recorded=None,skipped_attempts=None,
                    kernel_candidate_count=None,review_required=False,error=None)
                progress['groups'].append(row)
                progress['state']='running'
                publish()
                def capture(receiver,directory):
                    row['campaign_id']=directory.name
                    publish()
                try:
                    group_config = replace(config,families=(group['family'],),iterations=len(group['selection']))
                    result = pipeline(group_config,stop=stop,skip=skip,on_capture=capture,expected_selection=group['selection'])
                    if type(result) is not dict:raise RuntimeError('Invalid pipeline result')
                    reported = result.get('campaign_id')
                    if row['campaign_id'] is not None and reported is not None and reported!=row['campaign_id']:
                        raise RuntimeError('Pipeline campaign does not match captured campaign')
                    row['campaign_id']=reported or row['campaign_id']
                    outcomes = result.get('outcomes')
                    if type(outcomes) is list:
                        row['attempts_recorded']=len(outcomes)
                        row['skipped_attempts']=sum(value.get('skipped') is True for value in outcomes)
                    error = result.get('execution_error') or result.get('analysis_error')
                    if error:raise RuntimeError(str(error))
                    if stop.is_set():
                        row['state']='stopped'
                    else:
                        if result.get('stopped'):raise RuntimeError('Group stopped without operator Stop')
                        if row['attempts_recorded']!=row['planned']:raise RuntimeError('Group did not record every planned attempt')
                        candidates = analysis_candidates(result)
                        row.update(kernel_candidate_count=candidates,review_required=bool(candidates),state='completed')
                        progress['kernel_candidate_count']+=candidates
                        progress['review_required']=progress['review_required'] or bool(candidates)
                        progress['completed_groups']+=1
                except Exception as error:
                    row.update(state='failed',error=str(error))
                    progress['failed_groups']+=1
                progress['processed_groups']+=1
                publish()
                if stop.is_set():
                    progress['state']='stopped'
                    break
                # A pending skip belongs to the finished group, not the next one.
                skip.clear()
            else:progress['state']='completed'
            publish()
        except Exception as error:
            progress.update(state='failed',error=str(error))
            if progress['groups'] and progress['groups'][-1]['state']=='running':progress['groups'][-1]['state']='failed'
            write_snapshot(root/'progress.json',progress)
        return progress
    finally:
        os.close(fd)


def main(argv=None):
    from .dual_config import load
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--mode',choices=('uart','ssh'))
    parser.add_argument('--all-groups',action='store_true',help='Ignore configured family/profile filters')
    parser.add_argument('--execute',action='store_true',help='Actually start USB attempts on the isolated stand')
    args = parser.parse_args(argv)
    config = load(args.config,args.mode)
    if args.all_groups:config=replace(config,families=(),profiles=())
    if not args.execute:
        groups=make_plan(config)
        print(json.dumps(dict(execute=False,groups=[dict(family=g['family'],attempts=len(g['selection'])) for g in groups],planned_attempts=sum(len(g['selection']) for g in groups)),ensure_ascii=False))
        return 0
    stop,skip = threading.Event(),threading.Event()
    previous = {sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    if hasattr(signal,'SIGUSR1'):previous[signal.SIGUSR1]=signal.signal(signal.SIGUSR1,lambda *_:skip.set())
    try:
        result=run_batch(config,stop=stop,skip=skip,on_progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True))
        return 130 if result['state']=='stopped' else 0 if result['state']=='completed' and not result['failed_groups'] else 1
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


if __name__=='__main__':
    raise SystemExit(main())
