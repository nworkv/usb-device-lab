"""Unified lab.toml configuration and the `python -m usb_device_lab.lab` CLI.

Commands:
  init    write lab.toml and create var/ directories
  check   gadget/Raspberry Pi 4 checks and remote host checks over SSH
  fuzz    coverage-guided campaign; host agent, KCOV collector and kernel-log
          collection are started and stopped on the Linux host over SSH
  replay  emulate one device JSON through Raw Gadget (no coverage)
"""
from dataclasses import dataclass,replace
from pathlib import Path
import argparse
import ipaddress
import json
import re
import shlex
import subprocess
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib=None

STRATEGIES={'round-robin','weighted','coverage-guided'}
NAME=re.compile(r'^[A-Za-z0-9._@:-]{1,127}$')
USER=re.compile(r'^[a-z_][a-z0-9_.-]{0,31}$')

@dataclass(frozen=True)
class LabConfig:
    udc: str
    device_config: Path
    host: str
    ssh_user: str
    usb_bus: int
    seed_dir: Path
    corpus_out: Path
    results_dir: Path
    logs_dir: Path
    strategy: str
    udc_driver: str = ''
    profile: str = ''
    ssh_port: int = 22
    ssh_options: tuple = ()
    remote_dir: str = '/opt/usb-device-lab'
    remote_python: str = 'python3'
    collector: str = ''
    sudo: bool = True
    families: tuple = ()
    profiles: tuple = ()
    manifest: Path = None
    iterations: int = 100
    seconds: float = 5.0
    seed: int = 0
    path: Path = None

    @property
    def remote_collector(self): return self.collector or self.remote_dir.rstrip('/')+'/build/kcov-remote'
    @property
    def gadget_udc_driver(self): return self.udc_driver or self.udc

def _toml(path):
    if tomllib is None: raise RuntimeError('Python 3.11+ is required to read lab.toml')
    with Path(path).open('rb') as f: return tomllib.load(f)

def _names(value,what):
    if value is None: return ()
    if isinstance(value,str) or not all(isinstance(x,str) for x in value): raise ValueError(f'{what} must be a list of strings')
    return tuple(value)

def load(path):
    path=Path(path).resolve()
    raw=_toml(path)
    try:
        gadget,host,corpus,run=(raw[x] for x in ('gadget','host','corpus','run'))
        campaign=raw.get('campaign',{})
        address=str(host['address']);ipaddress.ip_address(address)
        bus=int(host['usb_bus'])
        if not 1<=bus<=255: raise ValueError('host.usb_bus must be 1..255')
        user=str(host.get('ssh_user','root'))
        if not USER.match(user): raise ValueError('invalid host.ssh_user')
        udc=str(gadget['udc'])
        if not NAME.match(udc): raise ValueError('invalid gadget.udc')
        driver=str(gadget.get('udc_driver',''))
        if driver and not NAME.match(driver): raise ValueError('invalid gadget.udc_driver')
        port=int(host.get('ssh_port',22))
        if not 1<=port<=65535: raise ValueError('host.ssh_port must be 1..65535')
        options=_names(host.get('ssh_options',()),'host.ssh_options')
        if any(not o.startswith('-o') for o in options): raise ValueError('host.ssh_options entries must start with -o')
        remote_dir=str(host.get('remote_dir','/opt/usb-device-lab'))
        if not remote_dir.startswith('/'): raise ValueError('host.remote_dir must be absolute')
        strategy=str(corpus.get('strategy','round-robin'))
        if strategy not in STRATEGIES: raise ValueError('unsupported corpus.strategy')
        iterations=int(campaign.get('iterations',100));seconds=float(campaign.get('seconds',5))
        if iterations<1 or seconds<=0: raise ValueError('campaign.iterations and campaign.seconds must be positive')
        base=path.parent
        rel=lambda v: (base/str(v)).resolve()
        seed_dir=rel(corpus['seed_dir'])
        manifest=rel(corpus['manifest']) if 'manifest' in corpus else (seed_dir.parent/'manifest.toml')
        return LabConfig(udc,rel(gadget['device_config']),address,user,bus,seed_dir,
            rel(run.get('corpus_out','var/corpus')),rel(run.get('results_dir','var/results')),rel(run.get('logs_dir','var/logs')),strategy,
            udc_driver=driver,profile=str(gadget.get('profile','')),ssh_port=port,ssh_options=options,remote_dir=remote_dir,
            remote_python=str(host.get('python','python3')),collector=str(host.get('collector','')),sudo=bool(host.get('sudo',True)),
            families=_names(corpus.get('families'),'corpus.families'),profiles=_names(corpus.get('profiles'),'corpus.profiles'),
            manifest=manifest,iterations=iterations,seconds=seconds,seed=int(campaign.get('seed',0)),path=path)
    except (KeyError,TypeError,ValueError) as e:
        raise ValueError(f'invalid lab config {path}: {e}') from e

def create_directories(config):
    for p in (config.corpus_out,config.results_dir,config.logs_dir): p.mkdir(parents=True,exist_ok=True)

TEMPLATE="""[gadget]
udc = "{udc}"
udc_driver = "{udc_driver}"
device_config = "{device_config}"
profile = "{profile}"

[host]
address = "{address}"
ssh_user = "{ssh_user}"
ssh_port = {ssh_port}
usb_bus = {usb_bus}
remote_dir = "{remote_dir}"
python = "python3"
sudo = {sudo}

[corpus]
seed_dir = "corpus/seeds"
manifest = "corpus/manifest.toml"
strategy = "coverage-guided"
# families = ["hid", "cdc"]

[campaign]
iterations = {iterations}
seconds = {seconds}
seed = 0

[run]
corpus_out = "var/corpus"
results_dir = "var/results"
logs_dir = "var/logs"
"""

def render(address,udc,usb_bus=1,ssh_user='root',udc_driver='',device_config='corpus/seeds/hid-keyboard.json',profile='raspberry-pi-4',
           ssh_port=22,remote_dir='/opt/usb-device-lab',sudo=True,iterations=100,seconds=5):
    ipaddress.ip_address(address)
    if not NAME.match(udc) or (udc_driver and not NAME.match(udc_driver)): raise ValueError('invalid UDC name')
    if not USER.match(ssh_user): raise ValueError('invalid SSH user')
    if not 1<=int(usb_bus)<=255: raise ValueError('USB bus must be 1..255')
    for v in (device_config,profile,remote_dir):
        if '"' in v or '\\' in v or '\n' in v: raise ValueError('quotes and backslashes are not allowed')
    return TEMPLATE.format(udc=udc,udc_driver=udc_driver,device_config=device_config,profile=profile,address=address,ssh_user=ssh_user,
        ssh_port=int(ssh_port),usb_bus=int(usb_bus),remote_dir=remote_dir,sudo='true' if sudo else 'false',iterations=int(iterations),seconds=float(seconds))

# ---------------------------------------------------------------- remote host
def ssh_destination(config): return f'{config.ssh_user}@{config.host}'

def ssh_argv(config,remote_command):
    return ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=10','-oServerAliveInterval=5','-oServerAliveCountMax=3',
            '-p',str(config.ssh_port),*config.ssh_options,'--',ssh_destination(config),remote_command]

def _sudo(config): return ['sudo','-n'] if config.sudo and config.ssh_user!='root' else []

def _remote(config,argv):
    return 'cd '+shlex.quote(config.remote_dir)+' && exec '+shlex.join(_sudo(config)+list(argv))

def agent_command(config):
    """Remote host agent: owns the KCOV collector and the /dev/kmsg reader for one run."""
    return _remote(config,['env','PYTHONPATH='+config.remote_dir,config.remote_python,'-m','usb_device_lab.host_agent',
                           '--bus',str(config.usb_bus),'--collector',config.remote_collector])

def agent_argv(config): return ssh_argv(config,agent_command(config))

def doctor_command(config):
    return _remote(config,['env','PYTHONPATH='+config.remote_dir,config.remote_python,'-m','usb_device_lab.doctor','host','--bus',str(config.usb_bus)])

def collector_check_command(config):
    return 'test -x '+shlex.quote(config.remote_collector)+' && echo collector-ok'

def dmesg_command(config): return _remote(config,['dmesg','--kernel'])

def cleanup_command(config):
    """Terminate stale agents/collectors for this bus left by an interrupted campaign."""
    sudo=shlex.join(_sudo(config))+' ' if _sudo(config) else ''
    agent=shlex.quote(f'usb_device_lab.host_agent --bus {config.usb_bus} ')
    collector=shlex.quote(f'^{re.escape(config.remote_collector)} {config.usb_bus}$')
    return f'{sudo}pkill -TERM -f {agent}; {sudo}pkill -TERM -f {collector}; true'

class HostOrchestrator:
    """Remote lifecycle on the Linux host under test.

    Per run, runner.execute() spawns agent_argv(); the agent starts the KCOV
    collector and kernel-log reader on `start` and stops both on `stop`.
    Around the whole campaign this class checks the host, kills stale
    agents/collectors and archives the host kernel log into logs_dir.
    """
    def __init__(self,config,run=subprocess.run):
        self.config=config;self.run=run
    def _ssh(self,command,timeout=30):
        return self.run(ssh_argv(self.config,command),capture_output=True,text=True,timeout=timeout)
    def check(self):
        report={'role':'remote-host','destination':ssh_destination(self.config)}
        try:
            r=self._ssh('true',15);report['ssh']={'ok':r.returncode==0,'detail':(r.stderr or '').strip()[-300:] or 'ssh ok'}
            if r.returncode: report['ready']=False;return report
            r=self._ssh(doctor_command(self.config))
            try: report['doctor']=json.loads(r.stdout)
            except ValueError: report['doctor']={'ready':False,'detail':(r.stderr or r.stdout).strip()[-500:]}
            r=self._ssh(collector_check_command(self.config))
            report['collector']={'ok':'collector-ok' in r.stdout,'detail':self.config.remote_collector}
        except (OSError,subprocess.SubprocessError) as e:
            report['ssh']={'ok':False,'detail':str(e)};report['ready']=False;return report
        report['ready']=bool(report['ssh']['ok'] and report['doctor'].get('ready') and report['collector']['ok'])
        return report
    def snapshot_kernel_log(self,name):
        self.config.logs_dir.mkdir(parents=True,exist_ok=True)
        target=self.config.logs_dir/f'host-dmesg-{name}-{time.strftime("%Y%m%dT%H%M%S")}.log'
        try:
            r=self._ssh(dmesg_command(self.config),60);target.write_text(r.stdout if r.returncode==0 else r.stderr)
        except (OSError,subprocess.SubprocessError) as e: target.write_text(f'kernel log unavailable: {e}\n')
        return target
    def cleanup(self):
        try: return self._ssh(cleanup_command(self.config),20).returncode
        except (OSError,subprocess.SubprocessError): return None
    def __enter__(self):
        self.cleanup();self.snapshot_kernel_log('start');return self
    def __exit__(self,*exc):
        self.cleanup();self.snapshot_kernel_log('stop');return False

# ---------------------------------------------------------------------- corpus
def read_manifest(path):
    return {str(f['name']):[str(p) for p in f.get('profiles',[])] for f in _toml(path).get('family',[])}

def select_seeds(config):
    """Seeds chosen by corpus.families / corpus.profiles; file name is <family>-<profile>.json."""
    families=read_manifest(config.manifest) if config.manifest and Path(config.manifest).exists() else {}
    if not families: raise ValueError(f'no families in corpus manifest {config.manifest}')
    unknown=set(config.families)-set(families)
    if unknown: raise ValueError('unknown corpus families: '+', '.join(sorted(unknown)))
    chosen=[]
    for name,profiles in families.items():
        if config.families and name not in config.families: continue
        for profile in profiles:
            if config.profiles and profile not in config.profiles and f'{name}/{profile}' not in config.profiles: continue
            chosen.append(config.seed_dir/f'{name}-{profile}.json')
    if not chosen: raise ValueError('corpus selection is empty')
    missing=[str(p) for p in chosen if not p.is_file()]
    if missing: raise ValueError('missing seed files: '+', '.join(missing))
    return chosen

def prepare_seed(config,source,directory):
    """Copy a device JSON with udc_driver/udc_device taken from lab.toml."""
    from .model import DeviceConfig
    data=DeviceConfig.load(source).data
    data['udc_driver']=config.gadget_udc_driver;data['udc_device']=config.udc
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/Path(source).name;DeviceConfig(data).save(target);return target

def prepare_seeds(config,directory=None):
    directory=Path(directory or config.corpus_out/'seeds')
    return [prepare_seed(config,s,directory) for s in select_seeds(config)]

# ------------------------------------------------------------------- commands
def cmd_init(a):
    out=Path(a.output)
    if out.exists() and not a.force: raise ValueError(f'{out} exists; use --force to overwrite')
    out.write_text(render(a.host,a.udc,a.bus,a.ssh_user,a.udc_driver or '',a.device_config,a.profile,a.ssh_port,a.remote_dir,not a.no_sudo,a.iterations,a.seconds))
    config=load(out);create_directories(config)
    print(json.dumps({'config':str(out),'corpus_out':str(config.corpus_out),'results_dir':str(config.results_dir),'logs_dir':str(config.logs_dir)},indent=2))
    return 0

def gather_checks(config,remote=True,orchestrator=None):
    from . import doctor
    report={'config':str(config.path),'gadget':doctor.gadget_report(config.udc)}
    if config.profile=='raspberry-pi-4': report['rpi4']=doctor.rpi4_report(config.udc)
    try: report['corpus']={'ok':True,'seeds':[str(p) for p in select_seeds(config)]}
    except ValueError as e: report['corpus']={'ok':False,'detail':str(e)}
    if remote: report['host']=(orchestrator or HostOrchestrator(config)).check()
    parts=[report['gadget']['ready'],report['corpus']['ok']]
    if 'rpi4' in report: parts.append(report['rpi4']['ready'])
    if remote: parts.append(report['host']['ready'])
    report['ready']=all(parts)
    return report

def summary(report):
    lines=[]
    def line(ok,text): lines.append(('OK   ' if ok else 'FAIL ')+text)
    g=report['gadget'];line(g['raw_gadget']['ok'],'/dev/raw-gadget');line(g['udc']['ok'],'UDC '+str(g.get('selected_udc')))
    for k,v in report.get('rpi4',{}).get('checks',{}).items(): line(v['ok'],f'rpi4 {k}: {v["detail"]}')
    c=report['corpus'];line(c['ok'],'corpus: '+(f'{len(c["seeds"])} seeds' if c['ok'] else c['detail']))
    if 'host' in report:
        h=report['host'];line(h.get('ssh',{}).get('ok',False),'ssh '+h['destination'])
        if 'doctor' in h: line(bool(h['doctor'].get('ready')),'host debugfs/kcov/kmsg')
        if 'collector' in h: line(h['collector']['ok'],'collector '+h['collector']['detail'])
    lines.append('READY' if report['ready'] else 'NOT READY: fix FAIL lines before starting a campaign')
    return '\n'.join(lines)

def cmd_check(a):
    config=load(a.config);report=gather_checks(config,remote=not a.local_only)
    print(json.dumps(report,indent=2) if a.json else summary(report))
    return 0 if report['ready'] else 1

def cmd_fuzz(a,campaign=None,orchestrator_cls=HostOrchestrator):
    config=load(a.config)
    if a.iterations: config=replace(config,iterations=a.iterations)
    if a.seconds: config=replace(config,seconds=a.seconds)
    if a.seed is not None: config=replace(config,seed=a.seed)
    create_directories(config)
    if not a.skip_checks:
        report=gather_checks(config,remote=True,orchestrator=orchestrator_cls(config))
        if not report['ready']:
            print(summary(report),file=sys.stderr);return 1
    seeds=prepare_seeds(config)
    if campaign is None: from .runner import campaign
    with orchestrator_cls(config):
        outcomes=campaign([str(s) for s in seeds],str(config.results_dir),agent_argv(config),config.iterations,config.seconds,config.seed)
    print(json.dumps({'runs':len(outcomes),'new_pcs':sum(o['new_pcs'] for o in outcomes),'with_errors':sum(1 for o in outcomes if o['errors'])}))
    return 0

def cmd_replay(a,device_cls=None):
    config=load(a.config)
    source=Path(a.input) if a.input else config.device_config
    if source.is_dir(): source=source/'config.json'
    target=prepare_seed(config,source,config.logs_dir/'replay')
    if device_cls is None: from .device import Device as device_cls
    from .model import DeviceConfig
    print(json.dumps({'replay':str(source),'prepared':str(target),'udc':config.udc}),flush=True)
    device_cls(DeviceConfig.load(target)).run()
    return 0

def build_parser():
    p=argparse.ArgumentParser(prog='python -m usb_device_lab.lab',description='USB Device Lab: lab.toml driven workflow')
    sub=p.add_subparsers(dest='command',required=True)
    i=sub.add_parser('init',help='write lab.toml and create var/ directories')
    i.add_argument('--host',required=True,help='IPv4/IPv6 address of the Linux host under test')
    i.add_argument('--udc',required=True,help='UDC name from /sys/class/udc (fe980000.usb on Raspberry Pi 4)')
    i.add_argument('--bus',type=int,default=1,help='host USB bus number the gadget is attached to')
    i.add_argument('--ssh-user',default='root');i.add_argument('--ssh-port',type=int,default=22)
    i.add_argument('--udc-driver',help='Raw Gadget driver name (defaults to --udc)')
    i.add_argument('--device-config',default='corpus/seeds/hid-keyboard.json')
    i.add_argument('--profile',default='raspberry-pi-4');i.add_argument('--remote-dir',default='/opt/usb-device-lab')
    i.add_argument('--no-sudo',action='store_true');i.add_argument('--iterations',type=int,default=100);i.add_argument('--seconds',type=float,default=5)
    i.add_argument('--output','-o',default='lab.toml');i.add_argument('--force',action='store_true')
    c=sub.add_parser('check',help='hardware, corpus and remote host checks');c.add_argument('--config',default='lab.toml')
    c.add_argument('--local-only',action='store_true',help='skip SSH checks');c.add_argument('--json',action='store_true')
    f=sub.add_parser('fuzz',help='run a coverage-guided campaign');f.add_argument('--config',default='lab.toml')
    f.add_argument('--iterations',type=int);f.add_argument('--seconds',type=float);f.add_argument('--seed',type=int)
    f.add_argument('--skip-checks',action='store_true')
    r=sub.add_parser('replay',help='emulate one device JSON or run directory');r.add_argument('--config',default='lab.toml')
    r.add_argument('--input',help='device JSON or var/results/runs/<id>; default gadget.device_config')
    return p

COMMANDS={'init':cmd_init,'check':cmd_check,'fuzz':cmd_fuzz,'replay':cmd_replay}

def main(argv=None):
    a=build_parser().parse_args(argv)
    try: return COMMANDS[a.command](a)
    except (ValueError,RuntimeError,FileNotFoundError) as e:
        print(f'error: {e}',file=sys.stderr);return 2

if __name__=='__main__': raise SystemExit(main())
