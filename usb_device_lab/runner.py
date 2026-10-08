import argparse
import fcntl
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from .errors import classify
from .model import DeviceConfig
from .mutator import Mutator
from .rpc import Lines
from .storage import Store

FATAL={'infrastructure','cleanup','coverage_unavailable'}


def run_summary(ident, result, new_pcs):
    """Keep legacy fields and expose the stored, unconfirmed triage verdict."""
    verdict = result.get('verdict') or {}
    return {
        'run': ident,
        'new_pcs': new_pcs,
        'errors': len(result.get('errors', [])),
        'outcome': verdict.get('outcome', 'inconclusive'),
        'kernel_event_count': len(result.get('kernel_events', [])),
        'confirmation': verdict.get('confirmation', 'unknown'),
        'coverage_valid': bool(result.get('coverage_valid')),
        'telemetry_complete': verdict.get('telemetry_complete', False),
    }


def apply_agent_reply(result, reply, namespace=None):
    """Preserve diagnostics before rejecting an agent/collector failure."""
    if not isinstance(reply, dict):
        raise RuntimeError('invalid host agent reply')
    errors = list(result.get('errors', []))
    result.update({key: value for key, value in reply.items() if key != 'errors'})
    result['errors'] = errors
    if reply.get('error'):
        result['coverage_valid'] = False
        result['pcs'] = []
        raise RuntimeError('host agent: ' + str(reply['error']))
    if namespace is not None and reply.get('namespace') != namespace:
        result['coverage_valid'] = False
        result['pcs'] = []
        raise RuntimeError('coverage namespace changed')
    return reply


def terminate(process, term_timeout=3, kill_timeout=8):
    """Stop our start_new_session child; SIGKILL cannot unblock kernel D state."""
    report = {'signals': [], 'forced': False, 'reaped': False}
    if process is None:
        return report
    if process.poll() is not None:
        process.wait(timeout=0)
        report['reaped'] = True
        return report
    for signum, timeout in ((signal.SIGTERM, term_timeout), (signal.SIGKILL, kill_timeout)):
        try:
            os.killpg(process.pid, signum)
            report['signals'].append(signal.Signals(signum).name)
            report['forced'] = signum == signal.SIGKILL
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
            report['reaped'] = True
            return report
        except subprocess.TimeoutExpired:
            if signum == signal.SIGKILL:
                raise RuntimeError('child did not exit after SIGKILL; inspect process state and kernel log')

def execute(directory,agent_argv,gadget_argv,seconds):
    directory=Path(directory);result={'coverage_valid':False,'pcs':[],'errors':[]};agent=gadget=None
    with open(directory/'agent.log','w+') as agent_log,open(directory/'executor.log','w+') as gadget_log:
        try:
            agent=subprocess.Popen(agent_argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=agent_log,text=True,bufsize=1,start_new_session=True)
            rpc=Lines(agent);hello=apply_agent_reply(result,rpc.receive())
            if not hello.get('ready'): raise RuntimeError('host agent not ready')
            rpc.send({'command':'start'})
            started=apply_agent_reply(result,rpc.receive())
            if not started.get('started'): raise RuntimeError('host coverage not started')
            gadget=subprocess.Popen(gadget_argv,stdout=gadget_log,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+seconds
            while gadget.poll() is None and time.monotonic()<deadline: time.sleep(0.05)
            result['window_completed']=gadget.poll() is None
            try:
                result['executor_stop']=terminate(gadget,term_timeout=8)
            except Exception as e:
                result['errors'].append({'kind':'cleanup','summary':str(e)})
            result['executor_returncode']=gadget.returncode
            time.sleep(0.3);rpc.send({'command':'stop'})
            apply_agent_reply(result,rpc.receive(timeout=30),namespace=hello.get('namespace'))
            agent.stdin.close();agent.wait(timeout=3)
        except Exception as e:
            result['coverage_valid']=False;result['errors'].append({'kind':'infrastructure','summary':str(e)})
        finally:
            for process in (gadget,agent):
                try: terminate(process)
                except Exception as e: result['errors'].append({'kind':'cleanup','summary':str(e)})
            for f in (gadget_log,agent_log): f.flush();os.fsync(f.fileno())
            gadget_log.seek(0);result['executor_log']=gadget_log.read(2*1024*1024)
            agent_log.seek(0);result['agent_log']=agent_log.read(16384)
    if any(e.get('kind') == 'cleanup' for e in result['errors']):
        result['coverage_valid']=False;result['pcs']=[]
    return classify(result)

def campaign(seeds,output,agent_argv,iterations,seconds,seed,gadget_module='usb_device_lab.device'):
    if seconds<=0 or iterations<1: raise ValueError('invalid campaign arguments')
    Path(output).mkdir(parents=True,exist_ok=True)
    with open(Path(output)/'campaign.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);store=Store(output)
        try:
            store.recover()
            for path in seeds: store.seed(DeviceConfig.load(path))
            if not store.corpus(): raise ValueError('no seeds in corpus')
            rng=random.Random(seed);mutator=Mutator(seed);outcomes=[]
            for iteration in range(iterations):
                parent=DeviceConfig.load(rng.choice(store.corpus()))
                config,mutation=(parent,[]) if iteration==0 else mutator.mutate(parent)
                ident=store.begin(config,{'parent':parent.digest,'mutation':mutation,'prng_seed':seed,'iteration':iteration})
                gadget=[sys.executable,'-u','-m',gadget_module,str(store.root/'runs'/ident/'config.json')]
                result=execute(store.root/'runs'/ident,agent_argv,gadget,seconds)
                result['iteration']=iteration;new=store.finish(ident,config,result)
                outcomes.append(run_summary(ident,result,new));print(json.dumps(outcomes[-1]),flush=True)
                if any(e['kind'] in FATAL for e in result['errors']): break
            return outcomes
        finally: store.close()

def main():
    p=argparse.ArgumentParser(description='USB Device Lab coverage-guided campaign')
    p.add_argument('--seeds',nargs='+',required=True);p.add_argument('--output',default='state')
    p.add_argument('--host',required=True,help='SSH destination of the Linux host under test')
    p.add_argument('--agent-command',required=True,help='remote command starting usb_device_lab.host_agent')
    p.add_argument('--iterations',type=int,default=100);p.add_argument('--seconds',type=float,default=5);p.add_argument('--seed',type=int,default=0)
    a=p.parse_args()
    if a.host.startswith('-'): raise SystemExit('invalid host')
    argv=['ssh','-T','-oBatchMode=yes','-oConnectTimeout=10',a.host,a.agent_command]
    campaign(a.seeds,a.output,argv,a.iterations,a.seconds,a.seed)

if __name__=='__main__': main()
