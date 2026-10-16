"""Source identity must include delegated mutation and speed-policy code."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from usb_device_lab import checkpoint_campaign as campaign


class EngineIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.populate(self.root)

    def populate(self, root):
        root.mkdir(parents=True, exist_ok=True)
        for name in campaign.ENGINE_SOURCES:
            (root/name).write_bytes(b'fixture source: '+name.encode('ascii'))

    def identity(self, root=None):
        root = self.root if root is None else root
        with patch.object(campaign,'__file__',str(root/'checkpoint_campaign.py')):
            return campaign.engine_identity()

    def test_delegated_dependencies_are_explicit_and_unique(self):
        self.assertIn('mutation_graph.py',campaign.ENGINE_SOURCES)
        self.assertIn('usb_speeds.py',campaign.ENGINE_SOURCES)
        self.assertEqual(len(campaign.ENGINE_SOURCES),len(set(campaign.ENGINE_SOURCES)))

    def test_mutation_graph_change_invalidates_identity(self):
        before = self.identity()
        path = self.root/'mutation_graph.py'
        original = path.read_bytes()
        path.write_bytes(original+b' changed operators')
        self.assertNotEqual(self.identity(),before)
        path.write_bytes(original)
        self.assertEqual(self.identity(),before)

    def test_speed_policy_change_invalidates_identity(self):
        before = self.identity()
        path = self.root/'usb_speeds.py'
        path.write_bytes(path.read_bytes()+b' changed speed policy')
        self.assertNotEqual(self.identity(),before)

    def test_missing_dependency_fails_closed(self):
        (self.root/'mutation_graph.py').unlink()
        with self.assertRaises(FileNotFoundError):
            self.identity()

    def test_identical_sources_at_another_root_have_same_identity(self):
        other = self.root/'relocated'
        self.populate(other)
        self.assertEqual(self.identity(),self.identity(other))

    def test_repeated_identity_is_stable_sha256(self):
        first = self.identity()
        self.assertRegex(first,r'^[0-9a-f]{64}$')
        self.assertEqual(self.identity(),first)


if __name__=='__main__':
    unittest.main()
