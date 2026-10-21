"""Runner cleanup closes RPC and executor pipes after child termination."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from usb_device_lab import runner

class FakeChild:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.stdin = tempfile.TemporaryFile('w+b')
        self.stdout = tempfile.TemporaryFile('w+b')
        self.stderr = tempfile.TemporaryFile('w+b')
        self.returncode = None
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.returncode = 0
        return 0

class Reader:
    owns_stdout = True
    def __init__(self, process):
        self.p = process
        self.replies = iter([
            {'ready': True, 'namespace': 'n'},
            {'started': True, 'namespace': 'n'},
            {'namespace': 'n', 'coverage_valid': True, 'pcs': ['0x1'], 'saturated': False},
        ])
    def receive(self, timeout=15):
        return next(self.replies)
    def send(self, value):
        pass
    def close(self):
        self.p.stdout.close()

class RunnerCleanupTests(unittest.TestCase):
    def test_executor_closes_every_subprocess_pipe(self):
        children, closed = [], []
        def popen(argv, **kwargs):
            child = FakeChild(argv, **kwargs)
            children.append(child)
            for name in ('stdin', 'stdout', 'stderr'):
                self.addCleanup(getattr(child, name).close)
            return child
        def cleanup(process, rpc=None):
            closed.append(process)
            if rpc:
                rpc.close()
            for name in ('stdin', 'stdout', 'stderr'):
                if name == 'stdout' and rpc:
                    continue
                getattr(process, name).close()
            return []
        def terminate_fake(process, *args, **kwargs):
            if process is not None:
                process.wait(timeout=0)
            return {'reaped': True}
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(runner.subprocess, 'Popen', side_effect=popen),
              patch.object(runner, 'Lines', Reader),
              patch.object(runner, 'close_rpc_streams', side_effect=cleanup),
              patch.object(runner, 'terminate', side_effect=terminate_fake),
              patch.object(runner.time, 'sleep'),
              patch.object(runner, 'classify', side_effect=lambda result: result)):
            result = runner.execute(Path(tmp), ['agent'], ['gadget'], 0)
        self.assertEqual([child.argv for child in children], [['agent'], ['gadget']])
        self.assertCountEqual(closed, children)
        self.assertTrue(all(child.stdin.closed and child.stdout.closed and child.stderr.closed for child in children))
        self.assertEqual(result['errors'], [], result)
        self.assertTrue(result['coverage_valid'], result)

if __name__ == '__main__':
    unittest.main()
