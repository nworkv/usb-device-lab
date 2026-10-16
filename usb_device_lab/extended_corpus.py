"""Build a separate baseline corpus with Low Speed HID and vendor bulk loopback."""
import argparse
import copy
import json
from pathlib import Path
from .corpus_variants import source_profiles, NAME, MAX_FILE
from .model import DeviceConfig
from .topology import validate_topology


def string_descriptor(index, text):
    payload = text.encode('utf-16-le')
    if len(payload) > 252:
        raise ValueError('USB string too long')
    return dict(type=3, index=index, wIndex=1033, hex=(bytes((len(payload)+2, 3))+payload).hex())


def low_speed(parent, profile, product):
    data = copy.deepcopy(parent.data)
    topology = validate_topology(data)
    for config in topology.values():
        for interface in config['interfaces']:
            for alternate in interface['alternates']:
                for endpoint in alternate.get('endpoints', []):
                    raw = bytes.fromhex(endpoint['descriptor_hex'])
                    if raw[3]&3 != 3 or not 1 <= int.from_bytes(raw[4:6], 'little') <= 8 or raw[6] < 10:
                        raise ValueError('Source HID runtime is not Low Speed compatible')
    device = next(d for d in data['descriptors'] if d['type']==1 and d.get('index',0)==0 and d.get('wIndex',0)==0)
    raw = bytearray.fromhex(device['hex'])
    if len(raw) != 18:
        raise ValueError('Expected an 18-byte device descriptor')
    raw[2:4] = (0x0110).to_bytes(2, 'little')
    raw[7] = 8
    device['hex'] = raw.hex()
    for descriptor in data['descriptors']:
        if descriptor['type']==3 and descriptor.get('index',0)>0:
            descriptor['wIndex'] = 1033
            if descriptor['index']==2:
                descriptor['hex'] = string_descriptor(2, product)['hex']
    data['speed'] = 1
    data['metadata'] = dict(family='hid', profile=profile, source_digest=parent.digest,
        description='Derived Low Speed HID baseline; hardware compatibility is unverified')
    return DeviceConfig(data)


def vendor_loopback():
    endpoints = [dict(descriptor_hex='07050102400000',read_length=64,interval_ms=1),
        dict(descriptor_hex='07058102400000',interval_ms=1)]
    interface = dict(number=0,alternates=[dict(setting=0,endpoints=endpoints)])
    data = dict(schema_version=1,udc_driver='dummy_udc',udc_device='dummy_udc.0',speed=2,
        metadata=dict(family='vendor',profile='bulk-loopback',description='Vendor interface: OUT 0x01 is queued unchanged for IN 0x81'),
        descriptors=[dict(type=1,hex='12010002000000406b1d9001000101020301'),
            dict(type=2,hex='0902200001010080320904000002ff0000000705010240000007058102400000'),
            dict(type=3,index=0,hex='04030904'),string_descriptor(1,'USB Device Lab'),
            string_descriptor(2,'Vendor bulk loopback'),string_descriptor(3,'0001')],
        runtime=dict(configurations=[dict(value=1,interfaces=[interface])]),
        protocol=dict(name='script',rules=[dict(event='out',address=1,capture='rx',send=[dict(address=129,var='rx')])]))
    return DeviceConfig(data)


def build(seed_dir, manifest, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError('Output must be a new directory')
    devices = {}
    for group, device in source_profiles(seed_dir, manifest):
        metadata = device.data.get('metadata')
        if type(metadata) is not dict:
            raise ValueError('Source seed metadata is required')
        family, profile = metadata.get('family'), metadata.get('profile')
        if type(family) is not str or type(profile) is not str or not NAME.fullmatch(family) or not NAME.fullmatch(profile) or family+'-'+profile != group:
            raise ValueError('Source metadata does not match manifest')
        devices[(family, profile)] = device
    additions = [('hid','keyboard-low-speed',low_speed(devices[('hid','keyboard')],'keyboard-low-speed','Low Speed HID keyboard')),
        ('hid','consumer-control-low-speed',low_speed(devices[('hid','consumer-control')],'consumer-control-low-speed','Low Speed consumer control')),
        ('vendor','bulk-loopback',vendor_loopback())]
    for family, profile, device in additions:
        if (family, profile) in devices:
            raise ValueError('Additional profile already exists: '+family+'/'+profile)
        devices[(family, profile)] = device
    families, entries, payloads = {}, [], {}
    for (family, profile), device in sorted(devices.items()):
        name = family+'-'+profile+'.json'
        payload = (device.canonical()+chr(10)).encode('utf-8')
        if len(payload) > MAX_FILE:
            raise ValueError('Extended seed exceeds 256 KiB')
        payloads[name] = payload
        families.setdefault(family, []).append(profile)
        entries.append(dict(family=family,profile=profile,path='seeds/'+name,digest=device.digest,speed=device.data.get('speed',2)))
    sections = ['[corpus]', 'format = 1', 'seed_dir = "seeds"', '']
    for family, profiles in sorted(families.items()):
        if len(profiles)>128:
            raise ValueError('Too many profiles in family')
        sections.extend(['[[family]]', 'name = '+json.dumps(family), 'profiles = '+json.dumps(profiles), ''])
    text = chr(10).join(sections)
    if len(text.encode())>65536 or len(families)>128 or len(entries)>128:
        raise ValueError('Extended baseline corpus exceeds supported selector bounds')
    output.mkdir(parents=True, exist_ok=False)
    seeds = output/'seeds'
    seeds.mkdir()
    for name, payload in payloads.items():
        with (seeds/name).open('xb') as stream:
            stream.write(payload)
    with (output/'manifest.toml').open('x',encoding='utf-8') as stream:
        stream.write(text)
    report = dict(schema_version=1,profile_count=len(entries),additional_profiles=len(additions),entries=entries,hardware_checked=False)
    with (output/'inventory.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    with (output/'complete.json').open('x',encoding='utf-8') as stream:
        json.dump(dict(profile_count=len(entries),hardware_checked=False),stream)
    return {key:value for key,value in report.items() if key!='entries'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-dir',default='corpus/seeds')
    parser.add_argument('--manifest',default='corpus/manifest.toml')
    parser.add_argument('--output',required=True)
    args = parser.parse_args(argv)
    try:
        report = build(args.seed_dir,args.manifest,args.output)
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(2,str(error)+chr(10))
    print(json.dumps(report))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
