"""Replay a saved executor configuration; this is not wire-traffic replay."""
import argparse
import fcntl
import hashlib
import json
import math
import sys
from pathlib import Path
from .errors import kernel_events
from .replay_verdict import summarize_replays
from .runner import execute
from .storage import atomic_write, write_json


def reproduce(run, output, agent_argv, attempts=3, seconds=5):
    if type(attempts) is not int or attempts < 2:
        raise ValueError('at least two attempts required')
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('positive finite seconds required')
    run = Path(run).resolve()
    output = Path(output).resolve()
    snapshot = (run / 'input.executed.json').read_text(encoding='utf-8')
    json.loads(snapshot)
    original = json.loads((run / 'result.json').read_text(encoding='utf-8'))
    expected = {event['header_fingerprint'] for event in kernel_events(original.get('kernel_log', ''))}
    if not expected:
        raise ValueError('source run has no kernel candidate headers')
    if output == run or run in output.parents:
        raise ValueError('output must be outside the source run')
    observations = []
    with open(run.parent.parent / 'campaign.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / 'replay.json', {
            'schema_version': 1, 'source_run': str(run),
            'input_sha256': hashlib.sha256(snapshot.encode('utf-8')).hexdigest(),
            'input_kind': 'executor_configuration',
            'expected_signatures': sorted(expected), 'attempts': attempts,
            'seconds': seconds,
        })
        write_json(output / 'confirmation.json', summarize_replays(expected, observations, attempts))
        for index in range(attempts):
            directory = output / ('attempt-%03d' % (index + 1))
            directory.mkdir()
            atomic_write(directory / 'input.executed.json', snapshot)
            gadget = [sys.executable, '-u', '-m', 'usb_device_lab.device',
                      str(directory / 'input.executed.json')]
            try:
                result = execute(directory, agent_argv, gadget, seconds)
            except Exception as error:
                result = {'errors': [{'kind': 'infrastructure', 'summary': str(error)}]}
            events = kernel_events(result.get('kernel_log', ''))
            kinds = {error.get('kind') for error in result.get('errors', [])}
            valid = (result.get('window_completed') is True
                     and (result.get('verdict') or {}).get('telemetry_complete') is True
                     and not result.get('log_dropped')
                     and not kinds.intersection({'infrastructure', 'cleanup', 'executor', 'executor_io', 'log_loss'}))
            observation = {'valid': valid,
                           'signatures': sorted({event['header_fingerprint'] for event in events})}
            write_json(directory / 'result.json', result)
            write_json(directory / 'observation.json', observation)
            observations.append(observation)
            report = summarize_replays(expected, observations, attempts)
            write_json(output / 'confirmation.json', report)
            if kinds.intersection({'infrastructure', 'cleanup'}):
                break
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', required=True, help='new directory for replay artifacts')
    parser.add_argument('--host', required=True)
    parser.add_argument('--agent-command', required=True)
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=5)
    args = parser.parse_args()
    if not args.host or args.host.startswith('-'):
        parser.error('invalid host')
    agent = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10', args.host, args.agent_command]
    print(json.dumps(reproduce(args.run, args.output, agent, args.attempts, args.seconds)), flush=True)


if __name__ == '__main__':
    main()
