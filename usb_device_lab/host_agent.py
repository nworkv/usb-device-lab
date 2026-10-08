import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from .host_logs import KernelLogs
from .rpc import Lines


def emit(value):
    print(json.dumps(value), flush=True)


def close_logs(logs):
    """Return available evidence even when finalizing the reader fails."""
    if logs is None:
        return {}
    try:
        return logs.close()
    except Exception as error:
        return {
            'kernel_log': ''.join(getattr(logs, 'records', [])),
            'log_dropped': max(1, getattr(logs, 'lost', 0)),
            'log_read_error': 'kernel log close failed: ' + str(error),
            'log_drain_incomplete': True,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bus', type=int, required=True)
    parser.add_argument('--collector', required=True)
    args = parser.parse_args()
    if not 1 <= args.bus <= 255:
        raise SystemExit('bus must be 1..255')
    namespace = f'{os.uname().release}:{Path("/proc/sys/kernel/random/boot_id").read_text().strip()}:{args.bus}'
    child = logs = None
    evidence = {}
    with tempfile.TemporaryFile(mode='w+') as stderr:
        try:
            emit({'ready': True, 'namespace': namespace})
            for line in sys.stdin:
                command = json.loads(line).get('command')
                if command == 'start' and child is None:
                    evidence = {}
                    logs = KernelLogs()
                    child = subprocess.Popen([args.collector, str(args.bus)], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
                    rpc = Lines(child)
                    if not rpc.receive().get('ready'):
                        raise RuntimeError('collector not ready')
                    rpc.send('start')
                    if not rpc.receive().get('started'):
                        raise RuntimeError('collector not started')
                    emit({'started': True, 'namespace': namespace})
                elif command == 'stop' and child is not None:
                    rpc.send('stop')
                    result = rpc.receive(timeout=30)
                    child.stdin.close()
                    child.wait(timeout=3)
                    if child.returncode:
                        raise RuntimeError('collector failed')
                    child = None
                    evidence = close_logs(logs)
                    result.update(evidence)
                    logs = None
                    result['pcs'] = sorted(set(result['pcs']))
                    result['namespace'] = namespace
                    result['coverage_valid'] = bool(result['pcs']) and not result['saturated']
                    emit(result)
                else:
                    raise ValueError('invalid agent state')
        except Exception as error:
            payload = {'error': str(error), 'namespace': namespace,
                       'coverage_valid': False, 'pcs': []}
            if child is not None:
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=3)
                except Exception as cleanup_error:
                    payload['collector_cleanup_error'] = str(cleanup_error)
                else:
                    child = None
            payload.update(evidence)
            payload.update(close_logs(logs))
            logs = None
            stderr.flush()
            stderr.seek(0)
            payload['collector_stderr'] = stderr.read(16384)
            emit(payload)
            return 1
        finally:
            if child is not None:
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=3)
                except Exception:
                    pass
            if logs is not None:
                close_logs(logs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
