import copy
import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch
from usb_device_lab.uart_config import from_dict, load, main


def valid():
    return {'gadget': {'udc': 'fe980000.usb', 'device_config': 'seeds/hid-keyboard.json'},
            'telemetry': {'mode': 'uart', 'schema_version': 1},
            'uart': {'port': '/dev/serial/by-id/test-adapter'},
            'corpus': {'seed_dir': 'seeds', 'manifest': 'manifest.toml'},
            'campaign': {}, 'run': {'results_dir': 'var/results-uart'}}


class UARTConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / 'lab.uart.toml'

    def parse(self, raw):
        return from_dict(raw, self.path)

    def test_defaults_and_relative_paths(self):
        config = self.parse(valid())
        self.assertEqual(config.telemetry_mode, 'uart')
        self.assertEqual(config.uart.baudrate, 115200)
        self.assertEqual((config.uart.data_bits, config.uart.parity, config.uart.stop_bits), (8, 'none', 1))
        self.assertEqual(config.seed_dir, self.root / 'seeds')
        self.assertEqual(config.results_dir, self.root / 'var/results-uart')
        self.assertEqual(config.gadget_udc_driver, 'fe980000.usb')
        self.assertFalse(config.results_dir.exists())

    def test_host_and_unknown_sections_rejected(self):
        for key in ('host', 'ssh', 'unexpected'):
            raw = valid()
            raw[key] = {}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(raw)

    def test_unknown_uart_fields_rejected(self):
        raw = valid()
        raw['uart']['agent_command'] = 'not allowed'
        with self.assertRaises(ValueError):
            self.parse(raw)

    def test_missing_sections_and_required_fields(self):
        raw = valid()
        del raw['uart']
        with self.assertRaises(ValueError):
            self.parse(raw)
        raw = valid()
        del raw['uart']['port']
        with self.assertRaises(ValueError):
            self.parse(raw)

    def test_invalid_mode_and_schema(self):
        for key, value in (('mode', 'ssh'), ('mode', True), ('schema_version', True), ('schema_version', 2)):
            raw = valid()
            raw['telemetry'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse(raw)

    def test_port_validation_without_device_open(self):
        for port in ('ttyUSB0', '/tmp/log', '/dev/../secret', '/dev/ttyUSB*', '/dev/', '/dev/ttyUSB0\n'):
            raw = valid()
            raw['uart']['port'] = port
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.parse(raw)

    def test_invalid_serial_numbers_and_booleans(self):
        for key, value in (('baudrate', 0), ('baudrate', True), ('data_bits', 9), ('stop_bits', 0),
                           ('read_timeout', 0), ('read_timeout', float('nan')),
                           ('reconnect_delay', float('inf')), ('read_chunk_bytes', 65537)):
            raw = valid()
            raw['uart'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse(raw)

    def test_invalid_parity(self):
        raw = valid()
        raw['uart']['parity'] = 'mark'
        with self.assertRaises(ValueError):
            self.parse(raw)

    def test_invalid_campaign_parameters(self):
        for key, value in (('iterations', 0), ('iterations', True), ('seconds', 0), ('seconds', 301),
                           ('seed', -1), ('seed', 2**63), ('post_run_capture_seconds', -1)):
            raw = valid()
            raw['campaign'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse(raw)

    def test_kcov_feedback_and_strategy_not_faked(self):
        raw = valid()
        raw['campaign']['feedback'] = 'kcov'
        with self.assertRaises(ValueError):
            self.parse(raw)
        raw = valid()
        raw['corpus']['strategy'] = 'coverage-guided'
        with self.assertRaises(ValueError):
            self.parse(raw)

    def test_names_and_output_collision(self):
        raw = valid()
        raw['corpus']['families'] = 'hid'
        with self.assertRaises(ValueError):
            self.parse(raw)
        raw = valid()
        raw['run']['logs_dir'] = raw['run']['results_dir']
        with self.assertRaises(ValueError):
            self.parse(raw)

    def test_no_host_properties_and_immutable_settings(self):
        config = self.parse(valid())
        for name in ('host', 'ssh_user', 'remote_dir', 'collector', 'usb_bus'):
            self.assertFalse(hasattr(config, name))
        with self.assertRaises(FrozenInstanceError):
            config.uart.baudrate = 9600

    def test_toml_loader_limits_and_cli_status(self):
        self.path.write_text('[gadget]\nudc="x"\ndevice_config="seed.json"\n[telemetry]\nmode="uart"\n[uart]\nport="/dev/ttyUSB0"\n[corpus]\nseed_dir="seeds"\nmanifest="manifest.toml"\n[campaign]\n[run]\nresults_dir="results"\n')
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--config', str(self.path)]), 0)
        status = json.loads(output.getvalue())
        self.assertTrue(status['configuration_valid'])
        self.assertFalse(status['hardware_checked'])
        self.assertFalse(status['runtime_connected'])
        self.path.write_bytes(b'x' * 65537)
        with self.assertRaises(ValueError):
            load(self.path)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(['--config', str(self.path)])
        self.assertEqual(caught.exception.code, 2)

    def test_no_serial_or_process_side_effects(self):
        with patch('os.open', side_effect=AssertionError('Device opened')), patch('subprocess.Popen', side_effect=AssertionError('Process started')):
            config = self.parse(valid())
        self.assertEqual(config.uart.port, '/dev/serial/by-id/test-adapter')


if __name__ == '__main__':
    unittest.main()
