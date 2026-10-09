import copy
import hashlib
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.checkpoint import (MAX_BYTES, capture_checkpoint, decode_checkpoint,
                                      encode_checkpoint, load_checkpoint, restore_generators, save_checkpoint)
from usb_device_lab.model import DeviceConfig
from usb_device_lab.mutator import Mutator

IDENTITY = 'a' * 64
ENGINE = 'b' * 64
CAMPAIGN = 'c' * 32
CORPUS = ['d' * 64, 'e' * 64]
PARAMETERS = {'iterations': 100, 'seconds': 5.0, 'seed': 42, 'families': ['hid'], 'profiles': []}


def expected():
    return dict(expected_identity=IDENTITY, expected_engine_id=ENGINE,
                expected_parameters=PARAMETERS, expected_corpus=CORPUS)


def capture(selection=None, mutation=None, iteration=1):
    return capture_checkpoint(IDENTITY, ENGINE, CAMPAIGN, PARAMETERS, CORPUS,
                              iteration, 'f'*32 if iteration else None,
                              selection or random.Random(42), mutation or random.Random(43))


class CheckpointTests(unittest.TestCase):
    def test_roundtrip_continues_both_sequences(self):
        selection, mutation = random.Random(42), random.Random(43)
        for _ in range(9):
            selection.choice(CORPUS)
            mutation.getrandbits(64)
        raw = encode_checkpoint(capture(selection, mutation))
        left, right = restore_generators(decode_checkpoint(raw, **expected()))
        self.assertEqual([selection.choice(CORPUS) for _ in range(100)], [left.choice(CORPUS) for _ in range(100)])
        self.assertEqual([mutation.getrandbits(64) for _ in range(100)], [right.getrandbits(64) for _ in range(100)])

    def test_gaussian_cache_survives(self):
        selection, mutation = random.Random(2), random.Random(3)
        selection.gauss(0, 1)
        restored, _ = restore_generators(capture(selection, mutation))
        self.assertEqual([selection.gauss(0, 1) for _ in range(20)], [restored.gauss(0, 1) for _ in range(20)])

    def test_real_mutator_trace_and_wire_seed_continue(self):
        parent = DeviceConfig({'schema_version': 1, 'udc_driver': 'dwc2', 'udc_device': 'test', 'speed': 2,
                               'descriptors': [{'type': 1, 'hex': '120100020000004000000000000001020301'},
                                               {'type': 2, 'hex': '090209000001008032'}]})
        original = Mutator(42)
        original.mutate(parent)
        value = capture(random.Random(5), original.random)
        _, restored_rng = restore_generators(decode_checkpoint(encode_checkpoint(value), **expected()))
        restored = Mutator(0)
        restored.random = restored_rng
        for _ in range(25):
            left, left_trace = original.mutate(parent)
            right, right_trace = restored.mutate(parent)
            self.assertEqual(left.data, right.data)
            self.assertEqual(left_trace, right_trace)

    def test_zero_and_completed_boundaries(self):
        self.assertIsNone(capture(iteration=0)['last_run'])
        self.assertEqual(capture(iteration=100)['next_iteration'], 100)
        with self.assertRaises(ValueError):
            capture(iteration=101)

    def test_compatibility_checks(self):
        raw = encode_checkpoint(capture())
        for key, value in (('expected_identity', '0'*64), ('expected_engine_id', '0'*64),
                           ('expected_corpus', ['0'*64]), ('expected_parameters', dict(PARAMETERS, seed=0))):
            values = expected()
            values[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                decode_checkpoint(raw, **values)

    def test_runtime_mismatch_and_boolean_schema(self):
        for update in ({'runtime': {}}, {'version': True}, {'next_iteration': True}, {'last_run': None}):
            value = capture()
            value.update(update)
            with self.assertRaises(ValueError):
                encode_checkpoint(value)

    def test_corrupt_rng_state_rejected(self):
        for key, replacement in (('version', True), ('mt', [0]*625), ('mt', [1]*624), ('gaussian', float('nan'))):
            value = capture()
            value['mutation_rng'][key] = replacement
            with self.assertRaises(ValueError):
                restore_generators(value)
        value = capture()
        value['selection_rng']['mt'][-1] = 625
        with self.assertRaises(ValueError):
            restore_generators(value)

    def test_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b'{"checkpoint":1,"checkpoint":2}', b'{"x":NaN}', b' '*(MAX_BYTES+1), bytes([255])):
            with self.assertRaises(ValueError):
                decode_checkpoint(raw, **expected())

    def test_checksum_detects_modified_payload(self):
        envelope = json.loads(encode_checkpoint(capture()))
        envelope['checkpoint']['next_iteration'] = 2
        with self.assertRaisesRegex(ValueError, 'checksum'):
            decode_checkpoint(json.dumps(envelope).encode(), **expected())

    def test_capture_copies_inputs_and_rejects_shared_rng(self):
        value = capture()
        original = copy.deepcopy(value)
        restored, _ = restore_generators(value)
        restored.random()
        self.assertEqual(value, original)
        same = random.Random(1)
        with self.assertRaises(ValueError):
            capture_checkpoint(IDENTITY, ENGINE, CAMPAIGN, PARAMETERS, CORPUS, 0, None, same, same)

    def test_numeric_seconds_normalized_and_corpus_sorted(self):
        value = capture()
        values = expected()
        values['expected_parameters'] = dict(PARAMETERS, seconds=5)
        values['expected_corpus'] = list(reversed(CORPUS))
        self.assertEqual(decode_checkpoint(encode_checkpoint(value), **values)['parameters']['seconds'], 5.0)
        bad = capture()
        bad['corpus'].append(bad['corpus'][0])
        with self.assertRaises(ValueError):
            encode_checkpoint(bad)

    def test_atomic_file_roundtrip_permissions_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'campaign-checkpoint.json'
            save_checkpoint(path, capture())
            self.assertEqual(load_checkpoint(path, **expected())['campaign_id'], CAMPAIGN)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('.checkpoint-*')), [])

    def test_replace_failure_keeps_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'campaign-checkpoint.json'
            save_checkpoint(path, capture())
            before = path.read_bytes()
            with patch('usb_device_lab.checkpoint.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    save_checkpoint(path, capture(iteration=2))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob('.checkpoint-*')), [])

    def test_file_fsync_failure_keeps_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'campaign-checkpoint.json'
            save_checkpoint(path, capture())
            before = path.read_bytes()
            with patch('usb_device_lab.checkpoint.os.fsync', side_effect=OSError('file sync failure')):
                with self.assertRaises(OSError):
                    save_checkpoint(path, capture(iteration=2))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob('.checkpoint-*')), [])

    def test_directory_fsync_failure_reports_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'campaign-checkpoint.json'
            save_checkpoint(path, capture())
            with patch('usb_device_lab.checkpoint.os.fsync', side_effect=[None, OSError('directory sync failure')]):
                with self.assertRaises(OSError):
                    save_checkpoint(path, capture(iteration=2))
            self.assertEqual(load_checkpoint(path, **expected())['next_iteration'], 2)
            self.assertEqual(list(Path(directory).glob('.checkpoint-*')), [])

    def test_invalid_parameters_and_corpus_capacity(self):
        for key, value in (('iterations', True), ('seconds', float('inf')), ('seed', -1), ('profiles', 'hid')):
            payload = capture()
            payload['parameters'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                encode_checkpoint(payload)
        payload = capture()
        payload['corpus'] = [f'{index:064x}' for index in range(4097)]
        with self.assertRaises(ValueError):
            encode_checkpoint(payload)

    def test_symlink_fifo_oversize_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            save_checkpoint(target, capture())
            link = root / 'link'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                load_checkpoint(link, **expected())
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                load_checkpoint(fifo, **expected())
            target.write_bytes(b'x'*(MAX_BYTES+1))
            with self.assertRaises(ValueError):
                load_checkpoint(target, **expected())
            with self.assertRaises(FileNotFoundError):
                load_checkpoint(root / 'missing', **expected())


if __name__ == '__main__':
    unittest.main()
