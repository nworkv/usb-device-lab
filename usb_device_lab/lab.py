from dataclasses import dataclass
from pathlib import Path
import ipaddress
import tomllib

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

def load(path):
    path=Path(path)
    with path.open('rb') as f: raw=tomllib.load(f)
    try:
        gadget,host,corpus,run=(raw[x] for x in ('gadget','host','corpus','run'))
        address=str(host['address']);ipaddress.ip_address(address)
        bus=int(host['usb_bus'])
        if not 1<=bus<=255: raise ValueError('host.usb_bus must be 1..255')
        strategy=str(corpus.get('strategy','round-robin'))
        if strategy not in {'round-robin','weighted','coverage-guided'}: raise ValueError('unsupported corpus.strategy')
        base=path.parent
        rel=lambda v: (base/str(v)).resolve()
        return LabConfig(str(gadget['udc']),rel(gadget['device_config']),address,str(host.get('ssh_user','root')),bus,rel(corpus['seed_dir']),rel(run.get('corpus_out','var/corpus')),rel(run.get('results_dir','var/results')),rel(run.get('logs_dir','var/logs')),strategy)
    except (KeyError,TypeError,ValueError) as e:
        raise ValueError(f'invalid lab config {path}: {e}') from e

def create_directories(config):
    for p in (config.corpus_out,config.results_dir,config.logs_dir): p.mkdir(parents=True,exist_ok=True)
