"""Reject writable batch directories before invoking any USB pipeline."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usb_device_lab import batch_campaign

class BatchDirectoryTests(unittest.TestCase):
    def test_group_or_world_writable_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root/'batches'
            parent.mkdir(mode=0o700)
            try:
                for mode in (0o720, 0o702):
                    with self.subTest(mode=mode):
                        parent.chmod(mode)
                        with patch.object(batch_campaign, 'make_plan', return_value=[{'selection': [1]}]):
                            with self.assertRaisesRegex(ValueError, 'Unsafe batch directory'):
                                batch_campaign.run_batch(SimpleNamespace(results_dir=root))
            finally:
                parent.chmod(0o700)

if __name__ == '__main__':
    unittest.main()
