"""Opt-in experimental SuperSpeed EP0 executor; no non-control endpoints."""
import argparse
import ctypes
import errno
from pathlib import Path
import re
import struct
from . import device as legacy
from .model import DeviceConfig
from .raw_io import INIT, RUN, FETCH, RawIO
from .topology import validate_topology
from .usb_speeds import read_speed_attribute


class ControlConfig(DeviceConfig):
    def validate(self):
        data = self.data
        if type(data) is not dict or type(data.get('speed')) is not int or data['speed'] != 5:
            raise ValueError('Experimental control-only mode requires speed=5')
        runtime = data.get('runtime')
        if type(runtime) is not dict or runtime.get('experimental_superspeed_control_only') is not True:
            raise ValueError('Explicit experimental_superspeed_control_only=true is required')
        # Reuse common storage/protocol validation, not its normal speed allowlist.
        projected = dict(data, speed=3)
        validated = DeviceConfig(projected)
        topology = validate_topology(validated.data)
        if any(alt.get('endpoints') for config in topology.values() for interface in config['interfaces'] for alt in interface['alternates']):
            raise ValueError('SuperSpeed control-only mode forbids all runtime data endpoints')
        if not any(d['type'] == 15 and d.get('index', 0) == 0 and d.get('wIndex', 0) == 0 for d in data['descriptors']):
            raise ValueError('A BOS descriptor selector is required')


class SuperSpeedControlDevice(legacy.Device):
    def __init__(self, config, io=None, sys_root=Path('/sys')):
        config = ControlConfig(config.data)
        udc = config.data['udc_device']
        if re.fullmatch(r'[A-Za-z0-9._@:-]{1,127}', udc) is None or udc in ('.', '..'):
            raise ValueError('Invalid UDC instance name')
        self.udc = Path(sys_root) / 'class' / 'udc' / udc
        super().__init__(config, io)

    def run(self):
        try:
            ControlConfig(self.data)
            maximum = read_speed_attribute(self.udc, 'maximum_speed')
            if not maximum['known'] or maximum['speed'] < 5:
                raise ValueError('Experimental SuperSpeed requires a known SuperSpeed-capable UDC ceiling')
            legacy.log(kind='experimental_superspeed', phase='precheck', maximum=maximum, control_only=True, hardware_acceptance=False)
            self.io = self.io or RawIO()
            init = ctypes.create_string_buffer(257)
            init[:257] = struct.pack('128s128sB', self.data['udc_driver'].encode(), self.data['udc_device'].encode(), 5)
            self.io.call(INIT, init)
            self.io.call(RUN)
            while True:
                event = ctypes.create_string_buffer(16)
                event[:8] = struct.pack('<II', 0, 8)
                self.io.call(FETCH, event)
                kind, size = struct.unpack('<II', event.raw[:8])
                if kind == 2 and size == 8:
                    current = read_speed_attribute(self.udc, 'current_speed')
                    if not current['known'] or current['speed'] != 5:
                        raise RuntimeError('SuperSpeed link is not confirmed by current_speed; no fallback or control response')
                    legacy.log(kind='experimental_superspeed', phase='control', observed=current, control_only=True, hardware_acceptance=False)
                    try:
                        self.control(event.raw[8:16])
                    except OSError as error:
                        legacy.log(kind='control_error', errno=error.errno, message=str(error))
                        if error.errno not in (errno.ECONNRESET, errno.ESHUTDOWN, errno.EINTR):
                            raise
                else:
                    legacy.log(kind='usb_event', event=kind)
                    if kind in (5, 6):
                        self.disable()
                        with self.lock:
                            self.protocol.reset()
        finally:
            try:
                if self.io is not None:
                    self.disable()
            finally:
                try:
                    self.protocol.close()
                finally:
                    if self.io is not None:
                        self.io.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        SuperSpeedControlDevice(ControlConfig.load(args.config)).run()
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, str(error) + chr(10))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
