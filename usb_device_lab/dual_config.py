"""Additive UART/SSH configuration; legacy UART loader stays unchanged."""
from dataclasses import dataclass, replace
from pathlib import Path
import copy
import ipaddress
import re
import tomllib
from .uart_config import UARTLabConfig, UARTSettings, from_dict as uart_from_dict

@dataclass(frozen=True)
class SSHSettings:
    host: str
    user: str
    identity_file: str
    known_hosts_file: str
    port: int = 22
    use_sudo: bool = True
    connect_timeout: int = 5
    read_timeout: float = 0.1
    read_chunk_bytes: int = 4096
    lease_seconds: int = 0

@dataclass(frozen=True)
class LabConfig(UARTLabConfig):
    ssh: SSHSettings | None = None

    @property
    def capture_settings(self):
        return self.uart if self.telemetry_mode == 'uart' else self.ssh


def ssh_settings(raw):
    required = {'host', 'user', 'identity_file', 'known_hosts_file'}
    optional = {'port', 'use_sudo', 'connect_timeout', 'read_timeout', 'read_chunk_bytes', 'lease_seconds'}
    if type(raw) is not dict or required - raw.keys() or raw.keys() - required - optional:
        raise ValueError('Invalid [ssh] fields; arbitrary commands/passwords are not supported')
    host, user = raw['host'], raw['user']
    if type(host) is not str or not host or len(host) > 253:
        raise ValueError('Invalid SSH host')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host) is None:
            raise ValueError('Invalid SSH host')
    if type(user) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', user) is None:
        raise ValueError('Invalid SSH user')
    for key in ('identity_file', 'known_hosts_file'):
        value = raw[key]
        if type(value) is not str or not value.startswith('/') or len(value) > 1024 or any(ord(c) < 32 for c in value):
            raise ValueError(key + ' must be an absolute trusted file path')
        if any(c in value for c in ('%', '~')) or any(c.isspace() for c in value) or '..' in Path(value).parts:
            raise ValueError(key + ': SSH path expansion/traversal is not supported')
    for key, low, high, default in (
        ('port', 1, 65535, 22), ('connect_timeout', 1, 8, 5),
        ('read_chunk_bytes', 64, 65536, 4096)):
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid ssh.' + key)
    lease = raw.get('lease_seconds', 0)
    if type(lease) is not int or lease != 0 and not 30 <= lease <= 3600:
        raise ValueError('ssh.lease_seconds: 0 disables lifetime; otherwise use 30..3600 seconds')
    timeout = raw.get('read_timeout', 0.1)
    if type(timeout) not in (int, float) or not 0.05 <= timeout <= 1:
        raise ValueError('Invalid ssh.read_timeout')
    if type(raw.get('use_sudo', True)) is not bool:
        raise ValueError('ssh.use_sudo must be boolean')
    return SSHSettings(**raw)


def from_dict(raw, path, mode=None):
    if type(raw) is not dict or type(raw.get('telemetry')) is not dict:
        raise ValueError('Missing [telemetry]')
    selected = mode if mode is not None else raw['telemetry'].get('mode')
    if selected not in ('uart', 'ssh'):
        raise ValueError('Choose telemetry.mode = uart or ssh')
    required = {'gadget', 'telemetry', 'corpus', 'campaign', 'run'}
    if required - raw.keys() or raw.keys() - required - {'uart', 'ssh'}:
        raise ValueError('Invalid top-level configuration sections')
    projected = copy.deepcopy(raw)
    projected.pop('ssh', None)
    projected['telemetry']['mode'] = 'uart'
    settings = None
    if selected == 'ssh':
        settings = ssh_settings(raw.get('ssh'))
        # Only common scalar/path validation uses this placeholder. Never opened.
        projected['uart'] = {'port': '/dev/null'}
    elif 'uart' not in projected:
        raise ValueError('UART mode requires [uart]')
    base = uart_from_dict(projected, path)
    if base.iterations > 10000:
        raise ValueError('Dual campaign supports at most 10000 attempts')
    value = LabConfig(**vars(base), ssh=settings)
    return replace(value, telemetry_mode=selected, uart=base.uart if selected == 'uart' else None)


def load(path, mode=None):
    path = Path(path)
    with path.open('rb') as f:
        data = f.read(65537)
    if len(data) > 65536:
        raise ValueError('Configuration exceeds 64 KiB')
    return from_dict(tomllib.loads(data.decode('utf-8')), path, mode)
