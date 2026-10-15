"""One entry point for checking, capture, CLI campaigns and operator panel."""
import argparse
import json
import signal
import threading
import uuid
from .dual_config import load


def main(argv=None):
    p = argparse.ArgumentParser(description='Agentless USB Lab: choose UART or SSH')
    p.add_argument('--config', required=True)
    p.add_argument('--mode', choices=('uart', 'ssh'))
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('check')
    run = sub.add_parser('run'); run.add_argument('--resume')
    capture = sub.add_parser('capture'); capture.add_argument('--output')
    panel = sub.add_parser('serve'); panel.add_argument('--port', type=int, default=8766)
    args = p.parse_args(argv)
    config = load(args.config, args.mode)
    if args.action == 'serve':
        from .research_service import serve
        serve(config, args.port); return 0
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if args.action == 'check':
            from .dual_preflight import inspect
            from .uart_pipeline import select_seeds
            result = inspect(config, [row['path'] for row in select_seeds(config)])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['metadata_checks_passed'] else 1
        if args.action == 'run':
            from .dual_pipeline import run_complete
            result = run_complete(config, resume=args.resume, stop=stop)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get('execution_error') or result.get('analysis_error') else 130 if stop.is_set() else 0
        from pathlib import Path
        from .dual_campaign import receiver_for
        directory = Path(args.output) if args.output else config.logs_dir / config.telemetry_mode / uuid.uuid4().hex
        receiver = receiver_for(config, directory)
        errors = []
        def receive():
            try:
                receiver.run()
            except Exception as error:
                errors.append(str(error))
        worker = threading.Thread(target=receive, name='standalone-capture', daemon=True)
        print('Capture directory: ' + str(directory), flush=True)
        worker.start()
        while worker.is_alive() and not stop.wait(0.1):
            pass
        receiver.stop(); worker.join(config.capture_settings.read_timeout + 4)
        if worker.is_alive():
            raise RuntimeError('Capture worker did not stop; inspect stand')
        if errors:
            raise RuntimeError('; '.join(errors))
        print(json.dumps(receiver.status(), ensure_ascii=False))
        return 130 if stop.is_set() else 0
    finally:
        for sig, previous_handler in previous.items():
            signal.signal(sig, previous_handler)

if __name__ == '__main__':
    raise SystemExit(main())
