"""Bootstrap smoke checks: no apt/Git/sudo/modules or real devices are invoked."""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from usb_device_lab.uart_config import load

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/pi_uart_setup.sh'

@unittest.skipUnless(shutil.which('bash'),'Bash required')
class UARTPiSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=self.root/'config/lab.uart.toml'
        self.repo=self.root/'repo';self.state=self.root/'state'
        self.text=SCRIPT.read_text()
        blocks=re.findall(r"<<'PY'\n(.*?)\nPY",self.text,re.S)
        self.generator=next(b for b in blocks if 'temporary=p.with_name' in b)

    def generate(self,baud='115200',replace='0'):
        argv=['embedded',str(self.config),str(self.repo),str(self.state),'/dev/serial/by-id/fixture',baud,'test.udc','test-driver','3',replace]
        before=sys.argv
        try:
            sys.argv=argv
            with redirect_stdout(io.StringIO()):exec(compile(self.generator,'config_generator','exec'),{})
        finally:sys.argv=before

    def test_bash_and_embedded_python_syntax(self):
        result=subprocess.run([shutil.which('bash'),'-n',str(SCRIPT)],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        for block in re.findall(r"<<'PY'\n(.*?)\nPY",self.text,re.S):compile(block,'embedded','exec')

    def test_help_and_bad_argument_have_no_setup_side_effects(self):
        env=dict(os.environ,HOME=str(self.root))
        result=subprocess.run([shutil.which('bash'),str(SCRIPT),'--help'],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        result=subprocess.run([shutil.which('bash'),str(SCRIPT),'--unknown-flag'],env=env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_generated_config_valid_absolute_paths_and_permissions(self):
        self.generate()
        value=load(self.config)
        self.assertEqual(value.udc,'test.udc')
        self.assertEqual(value.gadget_udc_driver,'test-driver')
        self.assertEqual(value.uart.baudrate,115200)
        self.assertEqual(value.manifest,self.repo/'corpus/manifest.toml')
        self.assertEqual(value.results_dir,self.state/'results')
        self.assertEqual(self.config.stat().st_mode & 0o777,0o600)

    def test_same_config_preserved(self):
        self.generate();before=self.config.read_bytes()
        with self.assertRaises(SystemExit) as error:self.generate()
        self.assertEqual(error.exception.code,0)
        self.assertEqual(self.config.read_bytes(),before)

    def test_different_config_requires_explicit_replace(self):
        self.generate();before=self.config.read_bytes()
        with self.assertRaises(SystemExit):self.generate(baud='9600')
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(list(self.config.parent.glob('*.backup-*')),[])

    def test_replace_creates_backup_and_new_valid_config(self):
        self.generate();before=self.config.read_bytes()
        self.generate(baud='9600',replace='1')
        backups=list(self.config.parent.glob('*.backup-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),before)
        self.assertEqual(load(self.config).uart.baudrate,9600)

if __name__=='__main__':unittest.main()
