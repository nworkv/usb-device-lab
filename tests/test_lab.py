import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
from usb_device_lab import lab

ROOT=Path(__file__).resolve().parents[1]

def write_lab(directory,**over):
    text=lab.render(over.pop('address','192.168.1.20'),over.pop('udc','fe980000.usb'),over.pop('usb_bus',3),over.pop('ssh_user','tester'),
                    device_config=str(ROOT/'corpus/seeds/hid-keyboard.json'))
    text=text.replace('seed_dir = "corpus/seeds"',f'seed_dir = "{ROOT/"corpus/seeds"}"').replace('manifest = "corpus/manifest.toml"',f'manifest = "{ROOT/"corpus/manifest.toml"}"')
    for old,new in over.items(): text=text.replace(old,new)
    path=Path(directory)/'lab.toml';path.write_text(text);return path

class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.dir=Path(self.tmp.name)
    def test_loads_rendered_config(self):
        c=lab.load(write_lab(self.dir))
        self.assertEqual((c.udc,c.host,c.ssh_user,c.usb_bus,c.strategy),('fe980000.usb','192.168.1.20','tester',3,'coverage-guided'))
        self.assertEqual(c.results_dir,(self.dir/'var/results').resolve());self.assertEqual(c.gadget_udc_driver,'fe980000.usb')
        self.assertEqual(c.remote_collector,'/opt/usb-device-lab/build/kcov-remote');self.assertEqual(c.iterations,100)
    def test_example_config_loads(self):
        self.assertEqual(lab.load(ROOT/'examples/lab.example.toml').usb_bus,1)
    def test_rejects_bad_values(self):
        for old,new in (('address = "192.168.1.20"','address = "host; rm -rf /"'),('usb_bus = 3','usb_bus = 0'),
                        ('strategy = "coverage-guided"','strategy = "random"'),('ssh_user = "tester"','ssh_user = "-oProxyCommand=x"'),
                        ('udc = "fe980000.usb"','udc = "a b"'),('remote_dir = "/opt/usb-device-lab"','remote_dir = "relative"')):
            with self.subTest(old=old), self.assertRaises(ValueError): lab.load(write_lab(self.dir,**{old:new}))
    def test_missing_section(self):
        p=self.dir/'lab.toml';p.write_text('[gadget]\nudc="x"\n')
        with self.assertRaises(ValueError): lab.load(p)
    def test_render_rejects_injection(self):
        with self.assertRaises(ValueError): lab.render('1.2.3.4','fe980000.usb',device_config='x"\n[evil]')

class RemoteCommandTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.c=lab.load(write_lab(tmp.name))
    def test_ssh_argv_uses_user_address_and_separator(self):
        argv=lab.agent_argv(self.c)
        self.assertEqual(argv[0],'ssh');self.assertIn('-oBatchMode=yes',argv);self.assertEqual(argv[argv.index('-p')+1],'22')
        self.assertEqual(argv[-3:-1],['--','tester@192.168.1.20'])
    def test_agent_command_contains_bus_and_collector(self):
        words=shlex.split(lab.agent_command(self.c))
        self.assertEqual(words[:3],['cd','/opt/usb-device-lab','&&'])
        self.assertIn('sudo',words);self.assertIn('usb_device_lab.host_agent',words)
        self.assertEqual(words[words.index('--bus')+1],'3');self.assertEqual(words[words.index('--collector')+1],'/opt/usb-device-lab/build/kcov-remote')
    def test_root_does_not_use_sudo(self):
        c=lab.load(write_lab(Path(self.c.path).parent,ssh_user='root'))
        self.assertNotIn('sudo',shlex.split(lab.agent_command(c)))
    def test_doctor_and_cleanup_commands(self):
        self.assertIn('--bus 3',lab.doctor_command(self.c))
        cleanup=lab.cleanup_command(self.c);self.assertIn('pkill',cleanup);self.assertIn('--bus 3',cleanup)
    def test_orchestrator_lifecycle(self):
        calls=[]
        def run(argv,**kw):
            calls.append(argv[-1]);out='{"ready": true}' if 'doctor' in argv[-1] else ('collector-ok\n' if 'test -x' in argv[-1] else 'log\n')
            return subprocess.CompletedProcess(argv,0,out,'')
        o=lab.HostOrchestrator(self.c,run)
        self.assertTrue(o.check()['ready'])
        with o: pass
        self.assertEqual(sum('pkill' in x for x in calls),2);self.assertEqual(sum('dmesg' in x for x in calls),2)
        self.assertEqual(len(list(self.c.logs_dir.glob('host-dmesg-*.log'))),2)
    def test_orchestrator_reports_ssh_failure(self):
        o=lab.HostOrchestrator(self.c,lambda argv,**kw: subprocess.CompletedProcess(argv,255,'','Permission denied'))
        r=o.check();self.assertFalse(r['ready']);self.assertIn('Permission denied',r['ssh']['detail'])

class CorpusTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.dir=Path(tmp.name)
    def test_every_manifest_profile_has_a_valid_seed(self):
        from usb_device_lab.model import DeviceConfig
        seeds=lab.select_seeds(lab.load(write_lab(self.dir)))
        self.assertEqual(len(seeds),sum(len(v) for v in lab.read_manifest(ROOT/'corpus/manifest.toml').values()))
        for s in seeds: DeviceConfig.load(s)
    def test_family_filter(self):
        c=lab.load(write_lab(self.dir,**{'# families = ["hid", "cdc"]':'families = ["hid", "cdc"]'}))
        names={p.name for p in lab.select_seeds(c)}
        self.assertIn('hid-keyboard.json',names);self.assertIn('cdc-acm.json',names);self.assertNotIn('uvc-streaming.json',names)
    def test_profile_filter_and_unknown_family(self):
        c=lab.load(write_lab(self.dir,**{'# families = ["hid", "cdc"]':'profiles = ["hid/mouse", "acm"]'}))
        self.assertEqual(sorted(p.name for p in lab.select_seeds(c)),['cdc-acm.json','hid-mouse.json'])
        c=lab.load(write_lab(self.dir,**{'# families = ["hid", "cdc"]':'families = ["nope"]'}))
        with self.assertRaises(ValueError): lab.select_seeds(c)
    def test_prepare_overrides_udc(self):
        c=lab.load(write_lab(self.dir,**{'udc_driver = ""':'udc_driver = "dwc2"'}))
        target=lab.prepare_seed(c,ROOT/'corpus/seeds/hid-mouse.json',self.dir/'out')
        data=json.loads(target.read_text());self.assertEqual((data['udc_driver'],data['udc_device']),('dwc2','fe980000.usb'))

class CliTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.dir=Path(tmp.name)
    def cli(self,*args):
        return subprocess.run([sys.executable,'-m','usb_device_lab.lab',*args],cwd=ROOT,capture_output=True,text=True,timeout=30,
                              env=dict(os.environ,PYTHONPATH=str(ROOT)))
    def test_help_lists_commands(self):
        r=self.cli('--help');self.assertEqual(r.returncode,0)
        for name in ('init','check','fuzz','replay'): self.assertIn(name,r.stdout)
    def test_init_creates_config_and_dirs(self):
        out=self.dir/'lab.toml'
        r=self.cli('init','--host','10.0.0.2','--udc','fe980000.usb','--bus','2','--output',str(out));self.assertEqual(r.returncode,0,r.stderr)
        c=lab.load(out);self.assertEqual(c.usb_bus,2);self.assertTrue(c.results_dir.is_dir() and c.logs_dir.is_dir())
        self.assertNotEqual(self.cli('init','--host','10.0.0.2','--udc','x','--output',str(out)).returncode,0)
    def test_init_rejects_bad_host(self):
        r=self.cli('init','--host','not-an-ip','--udc','x','--output',str(self.dir/'l.toml'));self.assertEqual(r.returncode,2)
    def test_check_local_only_reports_not_ready_without_hardware(self):
        r=self.cli('check','--config',str(write_lab(self.dir)),'--local-only')
        self.assertIn('corpus: 19 seeds',r.stdout);self.assertIn(r.returncode,(0,1))
    def test_fuzz_wires_lab_toml_to_campaign(self):
        c=write_lab(self.dir);seen={}
        class Orchestrator:
            entered=0
            def __init__(self,config): self.config=config
            def check(self): return {'ready':True,'destination':'x','ssh':{'ok':True}}
            def __enter__(self): Orchestrator.entered+=1;return self
            def __exit__(self,*e): Orchestrator.entered+=10
        def campaign(seeds,output,agent,iterations,seconds,seed):
            seen.update(seeds=seeds,output=output,agent=agent,iterations=iterations,seconds=seconds,seed=seed);return [{'new_pcs':1,'errors':0}]
        args=lab.build_parser().parse_args(['fuzz','--config',str(c),'--iterations','4','--skip-checks'])
        with redirect_stdout(io.StringIO()): self.assertEqual(lab.cmd_fuzz(args,campaign,Orchestrator),0)
        self.assertEqual(Orchestrator.entered,11);self.assertEqual(seen['iterations'],4);self.assertEqual(len(seen['seeds']),19)
        self.assertEqual(seen['output'],str(lab.load(c).results_dir));self.assertIn('tester@192.168.1.20',seen['agent'])
        self.assertTrue(all(json.loads(Path(s).read_text())['udc_device']=='fe980000.usb' for s in seen['seeds']))
    def test_fuzz_aborts_when_checks_fail(self):
        c=write_lab(self.dir)
        class Orchestrator:
            def __init__(self,config): pass
            def check(self): return {'ready':False,'destination':'x','ssh':{'ok':False}}
        args=lab.build_parser().parse_args(['fuzz','--config',str(c)])
        with redirect_stderr(io.StringIO()) as err: self.assertEqual(lab.cmd_fuzz(args,lambda *a: self.fail('campaign started'),Orchestrator),1)
        self.assertIn('NOT READY',err.getvalue())
    def test_replay_uses_input_and_udc(self):
        c=write_lab(self.dir);ran=[]
        class FakeDevice:
            def __init__(self,config): ran.append(config.data)
            def run(self): pass
        args=lab.build_parser().parse_args(['replay','--config',str(c),'--input',str(ROOT/'corpus/seeds/cdc-acm.json')])
        with redirect_stdout(io.StringIO()): self.assertEqual(lab.cmd_replay(args,FakeDevice),0)
        self.assertEqual(ran[0]['udc_device'],'fe980000.usb');self.assertEqual(ran[0]['metadata']['profile'],'acm')

if __name__=='__main__': unittest.main()
