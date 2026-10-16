"""Manifest selection, shared campaign and post-capture analysis for both modes."""
from pathlib import Path
from .dual_campaign import run_campaign, receiver_for
from .dual_preflight import inspect
from .uart_pipeline import select_seeds, prepare_seeds, analyze_capture, dump


def run_complete(config, *, resume=None, stop=None, preflight=True, command_factory=None,
                 receiver_factory=None, on_capture=None, on_stage=None,
                 lock_root=Path('/run/lock/usb-device-lab'), skip=None):
    stage = on_stage or (lambda value: None)
    stage('preparing')
    selection = select_seeds(config)
    if preflight:
        report = inspect(config, [r['path'] for r in selection], lock_root=lock_root)
        if report['metadata_checks_passed'] is not True:
            raise RuntimeError('Preflight failed: ' + str(report))
    if stop is not None and stop.is_set():
        return dict(stopped=True, execution_error=None, analysis_error=None)
    seeds = prepare_seeds(config, selection)
    captured = []
    def factory(settings, directory):
        receiver = receiver_factory(settings, directory) if receiver_factory else receiver_for(config, directory)
        captured.append((receiver, Path(directory)))
        if on_capture:
            on_capture(receiver, Path(directory))
        stage('running')
        return receiver
    result, error = None, None
    try:
        controls = dict(stop=stop, resume=resume, receiver_factory=factory,
            command_factory=command_factory, lock_root=lock_root)
        if skip is not None:
            controls['skip'] = skip
        result = run_campaign(config, seeds, **controls)
    except Exception as caught:
        error = str(caught)
    if not captured:
        if error:
            raise RuntimeError(error)
        return result
    receiver, capture = captured[-1]
    root = config.results_dir / capture.name
    result = result or dict(campaign_id=capture.name, directory=str(root))
    result.update(mode=config.telemetry_mode, execution_error=error, analysis_error=None)
    if receiver.status()['state'] not in ('stopped', 'failed'):
        raise RuntimeError('Capture is still active; analysis refused')
    dump(root / 'selection.json', selection)
    stage('analyzing')
    try:
        result['analysis'] = analyze_capture(config, root, capture, selection)
        dump(root / 'transport-analysis.json', dict(mode=config.telemetry_mode, causal=False,
            completeness='unknown', log_encoding='terminal bytes' if config.telemetry_mode == 'uart' else 'dmesg escaped text',
            clock='Pi receive-time monotonic; not host occurrence time'))
    except Exception as caught:
        result['analysis_error'] = str(caught)
    return result
