import tempfile
import unittest
from pathlib import Path
from usb_device_lab.event_journal import EventJournal


class EventJournalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'events.sqlite3'
        self.journal = EventJournal(self.path, capacity=3)
        self.addCleanup(self.journal.close)

    def test_empty_journal(self):
        result = self.journal.read()
        self.assertEqual(result['events'], [])
        self.assertEqual(result['next'], 0)
        self.assertFalse(result['gap'])

    def test_pagination_and_retention_gap(self):
        for count in range(5):
            self.journal.append({'runs': count})
        first = self.journal.read(0, 2)
        self.assertEqual([event['seq'] for event in first['events']], [3, 4])
        self.assertTrue(first['gap'])
        self.assertTrue(first['more'])
        second = self.journal.read(first['next'])
        self.assertEqual([event['seq'] for event in second['events']], [5])
        self.assertFalse(second['gap'])
        self.assertFalse(second['more'])

    def test_reopen_keeps_sequence(self):
        self.assertEqual(self.journal.append({'state': 'running'}), 1)
        self.journal.close()
        reopened = EventJournal(self.path, capacity=3)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.append({'state': 'completed'}), 2)
        self.assertEqual(len(reopened.read()['events']), 2)

    def test_invalid_cursors_and_limits(self):
        for after, limit in ((-1, 1), (True, 1), (1, 1), (0, 0), (0, 101), (2**63, 1)):
            with self.subTest(after=after, limit=limit), self.assertRaises(ValueError):
                self.journal.read(after, limit)

    def test_invalid_payload_leaves_journal_unchanged(self):
        for payload in ({'value': float('nan')}, {'value': 'x' * 16385}):
            with self.assertRaises(ValueError):
                self.journal.append(payload)
        self.assertEqual(self.journal.read()['events'], [])

    def test_close_is_idempotent(self):
        self.journal.close()
        self.journal.close()
        with self.assertRaises(RuntimeError):
            self.journal.append({})
        with self.assertRaises(RuntimeError):
            self.journal.read()


if __name__ == '__main__':
    unittest.main()
