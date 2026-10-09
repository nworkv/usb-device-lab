"""Integration tests of real dashboard/core components with synthetic USB hardware."""
import copy
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from usb_device_lab import checkpoint_campaign
from usb_device_lab.dashboard import DashboardServer
from usb_device_lab.event_control import EventCampaignSupervisor
from usb_device_lab.lab import load
from usb_device_lab.model import DeviceConfig
from usb_device_lab.errors import classify

TOKEN = 'integration-token-' + 'x' * 48


class SyntheticHardware:
    def __init__(self, block_at=None, kernel_log=''):
        self.block_at = block_at
        self.kernel_log = kernel_log
        self.inputs = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.preflights = 0
        self.opens = 0
        self.closes = 0
        self._lock = threading.Lock()

    def check(self, config):
        self.preflights += 1
        return {'ready': True}

    def orchestrator(self, config):
        hardware = self
        class Orchestrator:
            def __enter__(self):
                hardware.opens += 1
                return self
            def __exit__(self, *errors):
                hardware.closes += 1
                return False
        return Orchestrator()

    def execute(self, directory, agent, gadget, seconds):
        directory = Path(directory)
        device = DeviceConfig.load(directory / 'config.json')
        metadata = json.loads((directory / 'metadata.json').read_text())
        with self._lock:
            self.inputs.append((device.canonical(), metadata['parent'], metadata['mutation']))
            count = len(self.inputs)
        (directory / 'executor.log').write_text('fixture executor\n<script>not HTML</script>\n', encoding='utf-8')
        (directory / 'agent.log').write_text('fixture host agent\n', encoding='utf-8')
        if count == self.block_at:
            self.entered.set()
            if not self.release.wait(15):
                raise TimeoutError('Integration fixture was not released')
        return classify({'errors': [], 'coverage_valid': True, 'namespace': 'integration:synthetic-usb',
                'pcs': ['0x' + device.digest[:16]], 'saturated': False, 'log_dropped': False,
                'kernel_log': self.kernel_log, 'executor_returncode': 0, 'window_completed': True,
                'executor_log': 'fixture executor', 'agent_log': 'fixture host agent'})


class DashboardCheckpointIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hardware = SyntheticHardware()
        self.supervisor = None
        self.server = None
        self.server_thread = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.close_dashboard)
        real_session = checkpoint_campaign.run_checkpoint_session
        def hardware_session(config, stop, publish, resume=False):
            return real_session(config, stop, publish, resume=resume,
                                execute_fn=self.hardware.execute, checks_fn=self.hardware.check,
                                orchestrator_cls=self.hardware.orchestrator)
        self.stack.enter_context(patch('usb_device_lab.checkpoint_campaign.run_checkpoint_session',
                                      side_effect=hardware_session))

    def config(self, name='lab'):
        directory = self.root / name
        seeds = directory / 'seeds'
        seeds.mkdir(parents=True)
        data = {'schema_version': 1, 'udc_driver': 'dwc2', 'udc_device': 'fixture-udc', 'speed': 2,
                'descriptors': [{'type': 1, 'hex': '120100020000004000000000000001020301'},
                                {'type': 2, 'hex': '090209000001008032'}],
                'metadata': {'family': 'hid', 'profile': 'keyboard', 'description': 'Integration fixture'}}
        (seeds / 'hid-keyboard.json').write_text(json.dumps(data), encoding='utf-8')
        (directory / 'manifest.toml').write_text('[[family]]\nname = "hid"\nprofiles = ["keyboard"]\n', encoding='utf-8')
        text = """[gadget]
udc = "fixture-udc"
udc_driver = "dwc2"
device_config = "seeds/hid-keyboard.json"
profile = ""
[host]
address = "127.0.0.1"
ssh_user = "root"
usb_bus = 1
remote_dir = "/opt/usb-device-lab"
[corpus]
seed_dir = "seeds"
manifest = "manifest.toml"
strategy = "coverage-guided"
families = ["hid"]
[campaign]
iterations = 6
seconds = 1
seed = 42
[run]
corpus_out = "prepared"
results_dir = "results"
logs_dir = "logs"
"""
        path = directory / 'lab.toml'
        path.write_text(text, encoding='utf-8')
        return load(path)

    def open_dashboard(self, config):
        self.supervisor = EventCampaignSupervisor(config)
        self.server = DashboardServer(self.supervisor, TOKEN, config.results_dir, 0, config=config)
        self.server_thread = threading.Thread(target=self.server.serve_forever,
                                              kwargs={'poll_interval': 0.01}, daemon=True)
        self.server_thread.start()

    def close_dashboard(self):
        self.hardware.release.set()
        if self.supervisor is not None:
            self.supervisor.stop()
            thread = self.supervisor._thread
            if thread is not None:
                thread.join(10)
                if thread.is_alive():
                    raise RuntimeError('Integration campaign thread did not terminate')
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(5)
            self.server = None
        if self.supervisor is not None:
            self.supervisor.close()
            self.supervisor = None

    def request(self, method, path, data=None, authenticated=True, extra_headers=None):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + TOKEN
        headers.update(extra_headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, None if data is None else json.dumps(data), headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, body, dict(response.getheaders())
        finally:
            connection.close()

    def command(self, action, data=None, expected=202):
        code, body, _ = self.request('POST', '/api/campaign/' + action, {} if data is None else data)
        self.assertEqual(code, expected, body)
        return json.loads(body)

    def finish(self, expected='completed'):
        status = self.supervisor.wait(10)
        self.assertFalse(self.supervisor._thread.is_alive(), status)
        self.assertEqual(status['state'], expected, status)
        code, body, _ = self.request('GET', '/api/campaign/status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['state'], expected)
        return status

    def checkpoint(self, config):
        return json.loads((config.results_dir / 'campaign-checkpoint.json').read_text())['checkpoint']

    def row_count(self, config):
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            return db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]

    def paused_campaign(self, config):
        self.hardware.block_at = 2
        self.open_dashboard(config)
        self.command('start')
        self.assertTrue(self.hardware.entered.wait(5), self.supervisor.status())
        self.command('stop')
        self.hardware.release.set()
        status = self.finish('stopped')
        self.assertEqual(status['runs'], 2)
        self.assertEqual(self.checkpoint(config)['next_iteration'], 2)
        self.assertEqual(self.hardware.opens, self.hardware.closes)
        return self.checkpoint(config)['campaign_id']

    def test_http_stop_restart_resume_matches_uninterrupted_campaign(self):
        baseline = self.config('baseline')
        self.open_dashboard(baseline)
        self.command('start')
        self.finish()
        expected_inputs = copy.deepcopy(self.hardware.inputs)
        self.close_dashboard()
        self.hardware = SyntheticHardware()
        config = self.config('resumed')
        campaign_id = self.paused_campaign(config)
        before_resume = self.supervisor.status()['session']
        self.close_dashboard()
        self.open_dashboard(load(config.path))
        self.assertEqual(self.supervisor.status()['runs'], 2)
        self.command('resume')
        status = self.finish()
        self.assertEqual(status['runs'], 6)
        self.assertNotEqual(status['session'], before_resume)
        self.assertEqual(self.hardware.inputs, expected_inputs)
        self.assertEqual(self.checkpoint(config)['campaign_id'], campaign_id)
        self.assertEqual(self.checkpoint(config)['next_iteration'], 6)
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            rows = db.execute('SELECT iteration,run_id FROM campaign_attempts WHERE campaign_id=? ORDER BY iteration',
                              (campaign_id,)).fetchall()
            self.assertEqual([row[0] for row in rows], list(range(6)))
            self.assertEqual(len({row[1] for row in rows}), 6)
            self.assertEqual(self.checkpoint(config)['last_run'], rows[-1][1])
        self.assertEqual(self.row_count(config), 6)
        self.assertEqual(self.hardware.opens, self.hardware.closes)

    def test_results_corpus_logs_and_events_are_real_and_authorized(self):
        config = self.config()
        self.open_dashboard(config)
        self.command('start')
        self.finish()
        for path in ('/api/results', '/api/corpus?source=learned', '/api/campaign/events'):
            self.assertEqual(self.request('GET', path, authenticated=False)[0], 401)
            self.assertEqual(self.request('GET', path)[0], 200)
        rows = json.loads(self.request('GET', '/api/results')[1])
        self.assertEqual(len(rows), 6)
        run = rows[0]['id']
        code, body, _ = self.request('GET', '/api/logs/' + run + '/executor?lines=1')
        self.assertEqual(code, 200)
        self.assertIn('<script>not HTML</script>', json.loads(body)['text'])
        self.assertEqual(self.request('GET', '/api/logs/' + run, authenticated=False)[0], 401)
        corpus = json.loads(self.request('GET', '/api/corpus?source=learned')[1])
        self.assertTrue(corpus['items'])
        item = corpus['items'][0]
        self.assertTrue(item['valid'])
        self.assertEqual(item['family'], 'hid')
        code, body, _ = self.request('GET', '/api/corpus/learned/' + item['id'])
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['digest'], item['id'])
        events = json.loads(self.request('GET', '/api/campaign/events')[1])['events']
        self.assertTrue(any(event['status']['runs'] == 6 for event in events))
        last = self.checkpoint(config)['last_run']
        directory = config.results_dir / 'runs' / last
        for name in ('config.json', 'metadata.json', 'coverage.json', 'kernel_events.json', 'verdict.json', 'result.json', 'run.json', 'kmsg.delta.log'):
            self.assertTrue((directory / name).is_file(), name)
        snapshot = json.loads((config.results_dir / 'supervisor-state.json').read_text())
        self.assertEqual(snapshot['status']['runs'], 6)
        self.assertEqual(snapshot['status']['state'], 'completed')

    def test_kernel_evidence_is_unconfirmed_candidate_not_confirmed_bug(self):
        self.hardware.kernel_log = 'KASAN: slab-out-of-bounds in usb_fixture\nCall Trace:\n usb_fixture+0x1/0x20\n'
        config = self.config()
        self.open_dashboard(config)
        self.command('start')
        self.finish()
        rows = json.loads(self.request('GET', '/api/results')[1])
        self.assertTrue(all(row['outcome'] == 'kernel_candidate' for row in rows))
        self.assertTrue(all(row['confirmation'] == 'unconfirmed' for row in rows))
        directory = config.results_dir / 'runs' / self.checkpoint(config)['last_run']
        evidence = json.loads((directory / 'kernel_events.json').read_text())
        self.assertEqual(evidence[0]['kind'], 'kasan')
        self.assertEqual(evidence[0]['confirmation'], 'unconfirmed')

    def test_changed_corpus_resume_fails_without_executor_or_recovery(self):
        config = self.config()
        self.paused_campaign(config)
        path = next((config.results_dir / 'corpus').glob('*.json'))
        data = json.loads(path.read_text())
        data['udc_device'] = 'changed'
        path.write_text(json.dumps(data), encoding='utf-8')
        before = len(self.hardware.inputs)
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('digest', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), before)
        self.assertEqual(self.row_count(config), 2)

    def test_checkpoint_write_gap_is_not_automatically_replayed(self):
        config = self.config()
        self.open_dashboard(config)
        real_save = checkpoint_campaign.save_checkpoint
        def broken_save(path, value):
            if value['next_iteration'] > 0:
                raise OSError('integration checkpoint write failure')
            return real_save(path, value)
        with patch('usb_device_lab.checkpoint_campaign.save_checkpoint', side_effect=broken_save):
            self.command('start')
            status = self.finish('failed')
            self.assertIn('write failure', status['error'])
        self.assertEqual(self.row_count(config), 1)
        self.assertEqual(self.checkpoint(config)['next_iteration'], 0)
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('gap', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), 1)
        self.assertEqual(self.row_count(config), 1)

    def test_restart_keeps_events_and_requires_explicit_resume(self):
        config = self.config()
        self.paused_campaign(config)
        latest = json.loads(self.request('GET', '/api/campaign/events')[1])['latest']
        count = len(self.hardware.inputs)
        self.close_dashboard()
        self.open_dashboard(load(config.path))
        self.assertIsNone(self.supervisor._thread)
        self.assertEqual(len(self.hardware.inputs), count)
        data = json.loads(self.request('GET', '/api/campaign/events?after=' + str(latest))[1])
        self.assertGreater(data['latest'], latest)
        self.assertTrue(data['events'])

    def test_completed_budget_is_not_restarted_by_resume(self):
        config = self.config()
        self.open_dashboard(config)
        self.command('start')
        self.finish()
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('completed', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), 6)
        self.assertEqual(self.row_count(config), 6)

    def test_corrupted_checksum_resume_does_not_execute(self):
        config = self.config()
        self.paused_campaign(config)
        path = config.results_dir / 'campaign-checkpoint.json'
        envelope = json.loads(path.read_text())
        envelope['sha256'] = '0' * 64
        path.write_text(json.dumps(envelope), encoding='utf-8')
        before = len(self.hardware.inputs)
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('checksum', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), before)
        self.assertEqual(self.row_count(config), 2)

    def test_inconsistent_attempt_mapping_is_not_repaired(self):
        config = self.config()
        campaign_id = self.paused_campaign(config)
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            db.execute('DELETE FROM campaign_attempts WHERE campaign_id=? AND iteration=0', (campaign_id,))
        before = len(self.hardware.inputs)
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('inconsistent', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), before)
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_attempts WHERE campaign_id=?',
                                        (campaign_id,)).fetchone()[0], 1)

    def test_running_row_resume_refused_without_store_recover(self):
        config = self.config()
        self.paused_campaign(config)
        ident = self.checkpoint(config)['last_run']
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            db.execute("UPDATE runs SET status='running',finished=NULL WHERE id=?", (ident,))
        before = len(self.hardware.inputs)
        self.command('resume')
        status = self.finish('failed')
        self.assertIn('unfinished', status['error'].lower())
        self.assertEqual(len(self.hardware.inputs), before)
        with sqlite3.connect(config.results_dir / 'runs.sqlite3') as db:
            self.assertEqual(db.execute('SELECT status FROM runs WHERE id=?', (ident,)).fetchone()[0], 'running')

    def test_host_origin_and_read_routes_do_not_change_storage(self):
        config = self.config()
        self.open_dashboard(config)
        for path in ('/', '/corpus', '/logs', '/api/campaign/status'):
            self.assertEqual(self.request('GET', path, extra_headers={'Host': 'evil.test'})[0], 403)
            self.assertEqual(self.request('GET', path, extra_headers={'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('GET', '/api/results')[0], 200)
        self.assertFalse((config.results_dir / 'runs.sqlite3').exists())
        self.assertEqual(self.hardware.inputs, [])


if __name__ == '__main__':
    unittest.main()
