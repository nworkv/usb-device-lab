import argparse
from .model import DeviceConfig
from .mutator import Mutator
from .storage import Store

def main():
    p=argparse.ArgumentParser(description='USB Device Lab WIP core tools')
    sub=p.add_subparsers(dest='command',required=True)
    v=sub.add_parser('validate');v.add_argument('config')
    m=sub.add_parser('mutate');m.add_argument('config');m.add_argument('output');m.add_argument('--seed',type=int,default=0)
    s=sub.add_parser('seed');s.add_argument('config');s.add_argument('--state',default='state')
    a=p.parse_args();c=DeviceConfig.load(a.config)
    if a.command=='validate': print(c.digest)
    elif a.command=='mutate':
        child,trace=Mutator(a.seed).mutate(c);child.save(a.output);print(trace)
    else:
        store=Store(a.state)
        try: store.seed(c)
        finally: store.close()
if __name__=='__main__': main()
