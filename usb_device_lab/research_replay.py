"""Replay one saved configuration, not a promise of deterministic USB/host behaviour."""
from dataclasses import replace
import hashlib
import json
import math
import tempfile
from pathlib import Path
from .research_files import open_file, read_json, preview


def prepare(config,campaign,attempt):
    from .model import DeviceConfig
    with open_file(config,campaign,attempt,'config.json') as f:raw=f.read(1048577)
    if len(raw)>1048576:raise OverflowError('Конфигурация повтора больше 1 MiB')
    device=DeviceConfig(json.loads(raw))
    if device.data['udc_device']!=config.udc or device.data['udc_driver']!=config.gadget_udc_driver:
        raise ValueError('Контроллер исходной попытки отличается; точный повтор отклонён')
    meta=read_json(config,campaign,'campaign','campaign.json')
    seconds=meta.get('seconds')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=300:
        raise ValueError('Исходная длительность отсутствует или некорректна')
    try:
        old=read_json(config,campaign,attempt,'result.json')
    except FileNotFoundError:
        old={}
    original={key:old.get(key) for key in ('window_completed','interrupted','executor_returncode')}
    original['infrastructure_error']=str(old['infrastructure_error'])[:4096] if old.get('infrastructure_error') else None
    return raw,replace(config,iterations=1,seconds=float(seconds)),dict(
        campaign=campaign,attempt=attempt,config_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_digest=device.digest,source_mode=meta['mode'],mode=config.telemetry_mode,
        observation_changed=meta['mode']!=config.telemetry_mode,seconds=seconds,
        quiet_windows=dict(pre=getattr(config,'pre_run_quiet_seconds',None),post=getattr(config,'post_run_capture_seconds',None)),
        source_result=original,target=dict(udc=config.udc,driver=config.gadget_udc_driver),wire_seed=device.data.get('wire_mutator',{}).get('seed',0),
        causal=False,note='Повтор конфигурации и wire_seed; не запись/повтор USB-транзакций. Восстановите хост вручную.')


def run(config,raw,provenance,stop,on_capture,on_stage):
    from .model import DeviceConfig
    from .dual_campaign import run_campaign, receiver_for
    from .dual_preflight import inspect
    from .uart_pipeline import analyze_capture, dump
    captured=[];result=None;error=None
    config.results_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.replay-',dir=config.results_dir) as temporary:
        snapshot=Path(temporary)/'config.json';snapshot.write_bytes(raw);snapshot.chmod(0o600)
        report=inspect(config,[str(snapshot)])
        if not report['metadata_checks_passed']:raise RuntimeError('Предпроверка повтора не пройдена: '+str(report))
        if stop.is_set():return dict(stopped=True,execution_error=None,analysis_error=None)
        def factory(settings,directory):
            receiver=receiver_for(config,directory);captured.append((receiver,Path(directory)))
            dump(config.results_dir/Path(directory).name/'replay-source.json',provenance)
            on_capture(receiver,Path(directory));on_stage('running');return receiver
        try:
            result=run_campaign(config,[str(snapshot)],stop=stop,receiver_factory=factory)
        except Exception as caught:error=str(caught)
        if not captured:raise RuntimeError(error or 'Захват повтора не создан')
        receiver,capture=captured[-1];root=config.results_dir/capture.name
        if receiver.status()['state'] not in ('stopped','failed'):raise RuntimeError('Захват ещё активен; анализ запрещён')
        result=result or dict(campaign_id=capture.name,directory=str(root))
        result.update(execution_error=error,analysis_error=None,replay_source=provenance)
        same=DeviceConfig.load(root/'attempt-000000/config.json').digest==provenance['canonical_digest'] if (root/'attempt-000000/config.json').exists() else None
        on_stage('analyzing')
        selection=[dict(path=str(snapshot),profile='saved-attempt',digest=provenance['canonical_digest'])]
        dump(root/'selection.json',selection)
        try:result['analysis']=analyze_capture(config,root,capture,selection)
        except Exception as caught:result['analysis_error']=str(caught)
        windows={}
        for label,ident in [('source',provenance['campaign']),('repeat',capture.name)]:
            try:windows[label]=preview(config,ident,'capture','uart.raw',lines=1)['window_counts']
            except (OSError,ValueError):windows[label]=None
        comparison=dict(source=provenance,repeat=capture.name,same_configuration=same,
            execution_error=result['execution_error'],analysis_error=result['analysis_error'],
            last_64k_counts=windows,comparison_scope='last_64_KiB_of_each_capture_not_complete_or_attempt_specific',
            confirmed_reproduction=False,causal=False,completeness='unknown')
        dump(root/'replay-result.json',comparison)
        result['comparison']=comparison
        return result
