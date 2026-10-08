import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.reproduce import reproduce


def headers(log):
    return [{'header_fingerprint': 'candidate-A'}] if log == 'candidate' else []


def usable(log='candidate'):
    return {'kernel_log': log, 'window_completed': True,
            'verdict': {'telemetry_complete': True}, 'errors': []}


class ReproduceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'state' / 'runs' / 'source'
        self.run.mkdir(parents=True)
        self.snapshot = '{"saved": true}\n'
        (self.run / 'input.executed.json').write_text(self.snapshot, encoding='utf-8')
        (self.run / 'result.json').write_text(json.dumps({'kernel_log': 'candidate'}), encoding='utf-8')
        self.output = self.root / 'replay'
        self.header_patch = patch('usb_device_lab.reproduce.kernel_events', side_effect=headers)
        self.header_patch.start()
        self.addCleanup(self.header_patch.stop)

    def test_replays_same_snapshot_and_preserves_source(self):
        original = (self.run / 'result.json').read_bytes()
        with patch('usb_device_lab.reproduce.execute', side_effect=[usable(), usable(), usable('')]) as execute:
            report = reproduce(self.run, self.output, ['agent'], attempts=3)
        self.assertEqual(report['confirmation'], 'reproduced')
        self.assertEqual(report['matching_replays'], {'candidate-A': 2})
        self.assertEqual(execute.call_count, 3)
        for call in execute.call_args_list:
            directory, agent, gadget, seconds = call.args
            self.assertEqual(Path(gadget[-1]).read_text(encoding='utf-8'), self.snapshot)
            self.assertEqual(agent, ['agent'])
            self.assertTrue((directory / 'result.json').is_file())
        self.assertEqual((self.run / 'result.json').read_bytes(), original)
        self.assertEqual(json.loads((self.output / 'confirmation.json').read_text()), report)

    def test_infrastructure_failure_stops_and_is_inconclusive(self):
        with patch('usb_device_lab.reproduce.execute', side_effect=RuntimeError('offline')) as execute:
            report = reproduce(self.run, self.output, ['agent'])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report['confirmation'], 'inconclusive')
        self.assertEqual(report['usable'], 0)

    def test_existing_output_is_not_overwritten(self):
        self.output.mkdir()
        with patch('usb_device_lab.reproduce.execute') as execute:
            with self.assertRaises(FileExistsError):
                reproduce(self.run, self.output, ['agent'])
        execute.assert_not_called()

    def test_missing_candidate_is_rejected_before_execution(self):
        (self.run / 'result.json').write_text('{}', encoding='utf-8')
        with patch('usb_device_lab.reproduce.execute') as execute:
            with self.assertRaises(ValueError):
                reproduce(self.run, self.output, ['agent'])
        execute.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
