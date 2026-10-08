import json
import tempfile
import threading
import unittest
from pathlib import Path
from usb_device_lab.snapshot_schema import decode_snapshot
from usb_device_lab.persistent_control import PersistentCampaignSupervisor

IDENTITY = 'a' * 64


def snapshot():
    return {'version': 1, 'identity': IDENTITY,
            'status': {'state': 'completed', 'session': 'session', 'runs': 1, 'error': None, 'last_run': 'run'},
            'parameters': {'iterations': 2, 'seconds': 1.0, 'seed': 42, 'families': ['hid'], 'profiles': []}}


class SnapshotSchemaTests(unittest.TestCase):
    def decode(self, data):
        return decode_snapshot(json.dumps(data).encode(), IDENTITY)

    def test_valid_snapshot_and_normalized_selectors(self):
        status, parameters = self.decode(snapshot())
        self.assertEqual(status['runs'], 1)
        self.assertEqual(parameters['families'], ('hid',))

    def test_idle_snapshot_without_parameters(self):
        data = snapshot()
        data['parameters'] = None
        data['status'].update(state='idle', session=None, runs=0)
        self.assertIsNone(self.decode(data)[1])

    def test_duplicate_keys_and_nonfinite_json(self):
        for raw in (b'{"version":1,"version":1}', b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_snapshot(raw, IDENTITY)

    def test_invalid_root_version_and_identity(self):
        for key, value in (('version', True), ('version', 2), ('identity', 'wrong'), ('extra', 1)):
            data = snapshot()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.decode(data)

    def test_invalid_status_fields(self):
        for key, value in (('state', []), ('state', 'unknown'), ('runs', True), ('runs', -1), ('runs', 2**63),
                           ('session', {}), ('error', []), ('error', 'x' * 4097), ('last_run', 1), ('extra', 1)):
            data = snapshot()
            data['status'][key] = value
            with self.subTest(key=key, value=type(value)), self.assertRaises(ValueError):
                self.decode(data)
        data = snapshot()
        del data['status']['error']
        with self.assertRaises(ValueError):
            self.decode(data)

    def test_invalid_parameter_values(self):
        for key, value in (('iterations', True), ('iterations', 0), ('iterations', 1000001),
                           ('seconds', True), ('seconds', 0), ('seconds', 301), ('seconds', '1'),
                           ('seed', True), ('seed', -1), ('seed', 2**63), ('families', 'hid'),
                           ('families', [3]), ('profiles', ['']), ('profiles', ['x' * 128]),
                           ('profiles', ['hid'] * 129), ('extra', 'command')):
            data = snapshot()
            data['parameters'][key] = value
            with self.subTest(key=key, value=type(value)), self.assertRaises(ValueError):
                self.decode(data)

    def test_invalid_encoding_size_and_nesting(self):
        for raw in (bytes([255]), b' ' * 65537, b'[' * 2000 + b']' * 2000):
            with self.assertRaises(ValueError):
                decode_snapshot(raw, IDENTITY)

    def test_restore_rejects_before_mutating_status(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = object.__new__(PersistentCampaignSupervisor)
            supervisor._state_path = Path(directory) / 'state.json'
            supervisor._identity = IDENTITY
            supervisor._status = {'state': 'sentinel'}
            supervisor._last = None
            data = snapshot()
            data['parameters']['seconds'] = float('nan')
            supervisor._state_path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                supervisor._restore()
            self.assertEqual(supervisor._status, {'state': 'sentinel'})

    def test_save_rejects_invalid_parameters_before_replacing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = object.__new__(PersistentCampaignSupervisor)
            supervisor._directory = Path(directory)
            supervisor._state_path = Path(directory) / 'state.json'
            supervisor._state_path.write_bytes(b'previous snapshot')
            supervisor._identity = IDENTITY
            data = snapshot()
            supervisor._status = data['status']
            supervisor._last = data['parameters']
            supervisor._last['seconds'] = -1
            supervisor._persistence_error = None
            supervisor._stop = threading.Event()
            supervisor._save()
            self.assertEqual(supervisor._state_path.read_bytes(), b'previous snapshot')
            self.assertTrue(supervisor._stop.is_set())
            self.assertIn('Invalid saved seconds', supervisor._status['persistence_error'])

    def test_restore_active_state_remains_nonautomatic(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = object.__new__(PersistentCampaignSupervisor)
            supervisor._state_path = Path(directory) / 'state.json'
            supervisor._identity = IDENTITY
            data = snapshot()
            data['status']['state'] = 'running'
            supervisor._state_path.write_text(json.dumps(data))
            supervisor._restore()
            self.assertEqual(supervisor._status['state'], 'interrupted')
            self.assertEqual(supervisor._last['seed'], 42)


if __name__ == '__main__':
    unittest.main()
