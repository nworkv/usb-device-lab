import argparse
import sys
from .model import DeviceConfig
from .mutator import Mutator
from .storage import Store

def build_parser():
    p=argparse.ArgumentParser(prog='usb-device-lab',description='USB Device Lab (WIP): programmable USB device emulation fuzzing')
    sub=p.add_subparsers(dest='command',required=True)
    v=sub.add_parser('validate',help='validate a device JSON and print its SHA-256');v.add_argument('config')
    m=sub.add_parser('mutate',help='write one deterministic structural mutation');m.add_argument('config');m.add_argument('output');m.add_argument('--seed',type=int,default=0)
    s=sub.add_parser('seed',help='add a configuration to the corpus');s.add_argument('config');s.add_argument('--state',default='state')
    r=sub.add_parser('replay',help='emulate one configuration through Raw Gadget until interrupted');r.add_argument('config')
    f=sub.add_parser('fuzz',help='run a coverage-guided campaign against a Linux host over SSH')
    f.add_argument('--seeds',nargs='+',required=True);f.add_argument('--output',default='state')
    f.add_argument('--host',required=True,help='SSH destination of the Linux host under test')
    f.add_argument('--agent-command',required=True,help='remote command starting usb_device_lab.host_agent')
    f.add_argument('--iterations',type=int,default=100);f.add_argument('--seconds',type=float,default=5);f.add_argument('--seed',type=int,default=0)
    w=sub.add_parser('web',help='serve the read-only local dashboard on 127.0.0.1')
    w.add_argument('--output',default='state');w.add_argument('--port',type=int,default=8080)
    return p

def main(argv=None):
    a=build_parser().parse_args(argv)
    if a.command=='validate': print(DeviceConfig.load(a.config).digest)
    elif a.command=='mutate':
        child,trace=Mutator(a.seed).mutate(DeviceConfig.load(a.config));child.save(a.output);print(trace)
    elif a.command=='seed':
        store=Store(a.state)
        try: store.seed(DeviceConfig.load(a.config))
        finally: store.close()
    elif a.command=='replay':
        from .device import Device
        Device(DeviceConfig.load(a.config)).run()
    elif a.command=='fuzz':
        if a.host.startswith('-'): raise SystemExit('invalid host')
        from .runner import campaign
        argv=['ssh','-T','-oBatchMode=yes','-oConnectTimeout=10',a.host,a.agent_command]
        campaign(a.seeds,a.output,argv,a.iterations,a.seconds,a.seed)
    else:
        from .web import serve
        serve(a.output,a.port)

if __name__=='__main__': sys.exit(main())
