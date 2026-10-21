"""Regression coverage for subprocess cleanup failures and pipe ownership."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from usb_device_lab.rpc import close_rpc_streams

class CleanupErrorsTests(unittest.TestCase):
    def child(self):
        return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(),
                               stderr=io.BytesIO(), stdout_is_pipe=True)

    def test_without_reader_closes_all_owned_pipes(self):
        child = self.child()
        self.assertEqual(close_rpc_streams(child), [])
        self.assertTrue(all(getattr(child, name).closed
                            for name in ('stdin', 'stdout', 'stderr')))

    def test_close_failure_does_not_skip_remaining_pipes(self):
        child = self.child()
        child.stdin = Mock()
        child.stdin.close.side_effect = OSError('stdin failure')
        self.assertEqual(close_rpc_streams(child), ['stdin close: stdin failure'])
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        child.stdin.close.assert_called_once_with()

    def test_reader_timeout_preserves_reader_owned_stdout(self):
        child = self.child()
        reader = SimpleNamespace(owns_stdout=True, close_error=None,
                                 close=Mock(side_effect=RuntimeError('reader alive')))
        self.assertEqual(close_rpc_streams(child, reader), ['RPC close: reader alive'])
        self.assertFalse(child.stdout.closed)
        self.assertTrue(child.stdin.closed and child.stderr.closed)
        child.stdout.close()

    def test_recorded_reader_error_is_not_duplicated(self):
        child = self.child()
        reader = SimpleNamespace(owns_stdout=True, close_error=OSError('stdout failure'),
                                 close=Mock(side_effect=RuntimeError('wrapped error')))
        try:
            self.assertEqual(close_rpc_streams(child, reader),
                             ['RPC close: stdout failure'])
            self.assertTrue(child.stdin.closed and child.stderr.closed)
        finally:
            child.stdout.close()

    def test_cleanup_is_idempotent_without_reader(self):
        child = self.child()
        self.assertEqual(close_rpc_streams(child), [])
        self.assertEqual(close_rpc_streams(child), [])

    def test_missing_child_is_allowed(self):
        self.assertEqual(close_rpc_streams(None), [])

if __name__ == '__main__':
    unittest.main()
