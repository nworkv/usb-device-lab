import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/composite.json'

def run(*args):
    return subprocess.run([sys.executable,'-m','usb_device_lab',*args],cwd=ROOT,capture_output=True,text=True,timeout=20)

class CommandLineTests(unittest.TestCase):
    def test_validate(self):
        result=run('validate',str(EXAMPLE));self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(len(result.stdout.strip()),64)
    def test_validate_rejects_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            bad=Path(directory)/'bad.json';bad.write_text(json.dumps({'schema_version':2}))
            self.assertNotEqual(run('validate',str(bad)).returncode,0)
    def test_mutation_is_reproducible_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/'first.json';second=Path(directory)/'second.json'
            self.assertEqual(run('mutate',str(EXAMPLE),str(first),'--seed','5').returncode,0)
            self.assertEqual(run('mutate',str(EXAMPLE),str(second),'--seed','5').returncode,0)
            self.assertEqual(first.read_text(),second.read_text());self.assertEqual(run('validate',str(first)).returncode,0)
    def test_seed_adds_corpus_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(run('seed',str(EXAMPLE),'--state',directory).returncode,0)
            self.assertEqual(len(list((Path(directory)/'corpus').glob('*.json'))),1)
    def test_fuzz_rejects_option_like_host(self):
        result=run('fuzz','--seeds',str(EXAMPLE),'--host=-oProxyCommand=x','--agent-command','y')
        self.assertNotEqual(result.returncode,0);self.assertIn('invalid host',result.stderr)
    def test_help_lists_all_commands(self):
        result=run('--help');self.assertEqual(result.returncode,0)
        for name in ('validate','mutate','seed','replay','fuzz','web'): self.assertIn(name,result.stdout)

if __name__=='__main__': unittest.main()
