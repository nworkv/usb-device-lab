"""Storage contract tests; no USB hardware or SSH is required."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usb_device_lab.errors import classify
from usb_device_lab.storage import Store


class FixtureConfig:
    def canonical(self):
        return '{"fixture":true}'

    @property
    def digest(self):
        return hashlib.sha256(self.canonical().encode()).hexdigest()


class TriageStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.config = FixtureConfig()

    def tearDown(self):
        try:
            self.store.close()
        finally:
            self.tmp.cleanup()

    def finish_run(self, **changes):
        ident = self.store.begin(self.config)
        result = {
            'pcs': [4096, 8192],
            'namespace': 'kernel:boot:2',
            'coverage_valid': True,
            'errors': [],
        }
        result.update(changes)
        result = classify(result)
        new = self.store.finish(ident, self.config, result)
        return ident, result, new

    def test_new_and_repeated_coverage(self):
        _, result, new = self.finish_run()
        self.assertEqual(new, 2)
        self.assertEqual(result['verdict']['outcome'], 'new_coverage')
        _, result, new = self.finish_run()
        self.assertEqual(new, 0)
        self.assertEqual(result['verdict']['outcome'], 'no_change')

    def test_artifacts_and_database_match(self):
        ident, result, _ = self.finish_run(kernel_log='usb enumeration\n')
        directory = self.root / 'runs' / ident
        for name in (
            'config.json', 'input.executed.json', 'metadata.json',
            'run.json', 'coverage.json', 'kernel_events.json',
            'verdict.json', 'result.json', 'kmsg.delta.log',
        ):
            with self.subTest(artifact=name):
                self.assertTrue((directory / name).is_file())
        self.assertEqual(json.loads((directory / 'result.json').read_text()), result)
        self.assertEqual(json.loads((directory / 'verdict.json').read_text()), result['verdict'])
        self.assertEqual((directory / 'kmsg.delta.log').read_text(), 'usb enumeration\n')
        self.assertEqual((directory / 'input.executed.json').read_text(), self.config.canonical())
        row = self.store.db.execute('SELECT result,status FROM runs WHERE id=?', (ident,)).fetchone()
        self.assertEqual(json.loads(row['result']), result)
        self.assertEqual(row['status'], 'ok')

    def test_kernel_candidate_takes_priority(self):
        ident, result, new = self.finish_run(kernel_log='BUG: KASAN: test')
        self.assertEqual(new, 2)
        self.assertEqual(result['verdict']['outcome'], 'kernel_candidate')
        self.assertEqual(result['verdict']['confirmation'], 'unconfirmed')
        directory = self.root / 'runs' / ident
        events = json.loads((directory / 'kernel_events.json').read_text())
        self.assertEqual(events[0]['kind'], 'kasan')
        row = self.store.db.execute('SELECT status FROM runs WHERE id=?', (ident,)).fetchone()
        self.assertEqual(row['status'], 'error')

    def test_saturated_coverage_is_not_indexed(self):
        _, result, new = self.finish_run(saturated=True)
        self.assertEqual(new, 0)
        self.assertFalse(result['coverage_valid'])
        self.assertFalse(result['corpus_added'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM coverage').fetchone()[0], 0)

    def test_empty_coverage_is_infrastructure_failure(self):
        _, result, new = self.finish_run(pcs=[])
        self.assertEqual(new, 0)
        self.assertFalse(result['coverage_valid'])
        self.assertEqual(result['verdict']['outcome'], 'infrastructure_failure')

    def test_recovery_does_not_claim_kernel_bug(self):
        ident = self.store.begin(self.config)
        self.store.recover()
        directory = self.root / 'runs' / ident
        verdict = json.loads((directory / 'verdict.json').read_text())
        self.assertEqual(verdict['outcome'], 'interrupted')
        self.assertIsNone(verdict['kernel_evidence_detected'])
        self.assertFalse(verdict['telemetry_complete'])
        row = self.store.db.execute('SELECT status FROM runs WHERE id=?', (ident,)).fetchone()
        self.assertEqual(row['status'], 'interrupted')
        with self.assertRaises(ValueError):
            self.store.finish(ident, self.config, {'pcs': []})

    def test_finished_run_cannot_be_finished_twice(self):
        ident, result, _ = self.finish_run()
        with self.assertRaises(ValueError):
            self.store.finish(ident, self.config, result)

    def test_coverage_namespace_isolation(self):
        self.finish_run()
        _, result, new = self.finish_run(namespace='kernel:other-boot:2')
        self.assertEqual(new, 2)
        self.assertEqual(result['verdict']['outcome'], 'new_coverage')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM coverage').fetchone()[0], 4)

    def test_missing_namespace_rejects_result(self):
        ident = self.store.begin(self.config)
        with self.assertRaises(ValueError):
            self.store.finish(ident, self.config, {'pcs': [4096], 'coverage_valid': True})
        row = self.store.db.execute('SELECT status FROM runs WHERE id=?', (ident,)).fetchone()
        self.assertEqual(row['status'], 'running')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM coverage').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
