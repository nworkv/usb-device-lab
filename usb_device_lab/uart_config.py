"""Strict, side-effect-free configuration for the future agentless UART backend."""
import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
import tomllib

MAX_CONFIG_BYTES = 65536
SECTIONS = {'gadget', 'telemetry', 'uart', 'corpus', 'campaign', 'run'}


@dataclass(frozen=True)
class UARTSettings:
    port: str
    baudrate: int = 115200
    data_bits: int = 8
    parity: str = 'none'
    stop_bits: int = 1
    read_timeout: float = 0.25
    reconnect_delay: float = 1.0
    read_chunk_bytes: int = 4096


@dataclass(frozen=True)
class UARTLabConfig:
    path: Path
    udc: str
    device_config: Path
    seed_dir: Path
    manifest: Path
    corpus_out: Path
    results_dir: Path
    logs_dir: Path
    uart: UARTSettings
    udc_driver: str = ''
    profile: str = 'raspberry-pi-4'
    families: tuple = ()
    profiles: tuple = ()
    strategy: str = 'round-robin'
    iterations: int = 100
    seconds: float = 2.0
    seed: int = 0
    pre_run_quiet_seconds: float = 0.5
    post_run_capture_seconds: float = 1.0
    telemetry_mode: str = 'uart'
    feedback: str = 'none'

    @property
    def gadget_udc_driver(self):
        return self.udc_driver or self.udc


def table(raw, name, allowed, required=()):
    value = raw.get(name)
    if type(value) is not dict or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError('Некорректная секция [' + name + ']: неизвестные или отсутствующие поля')
    return value


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(name + ': требуется целое число от ' + str(low) + ' до ' + str(high))
    return value


def number(value, low, high, name, positive=False):
    if type(value) not in (int, float) or not low <= value <= high or positive and value == 0:
        raise ValueError(name + ': число вне допустимого диапазона')
    if not math.isfinite(value):
        raise ValueError(name + ': требуется конечное число')
    return float(value)


def names(value, name):
    if type(value) is not list or len(value) > 128 or any(type(item) is not str or not 1 <= len(item) <= 127 for item in value):
        raise ValueError(name + ': требуется список непустых строк')
    return tuple(value)


def text(value, name, empty=False):
    if type(value) is not str or not empty and not value or any(ord(char) < 32 for char in value) or len(value) > 1024 or value != value.strip():
        raise ValueError(name + ': некорректная строка')
    return value


def from_dict(raw, path):
    path = Path(path).resolve()
    if type(raw) is not dict or set(raw) != SECTIONS:
        raise ValueError('Нужны только секции gadget, telemetry, uart, corpus, campaign и run; [host] не допускается')
    gadget = table(raw, 'gadget', {'udc', 'udc_driver', 'device_config', 'profile'}, {'udc', 'device_config'})
    telemetry = table(raw, 'telemetry', {'mode', 'schema_version'}, {'mode'})
    serial = table(raw, 'uart', {'port', 'baudrate', 'data_bits', 'parity', 'stop_bits', 'read_timeout', 'reconnect_delay', 'read_chunk_bytes'}, {'port'})
    corpus = table(raw, 'corpus', {'seed_dir', 'manifest', 'families', 'profiles', 'strategy'}, {'seed_dir', 'manifest'})
    campaign = table(raw, 'campaign', {'iterations', 'seconds', 'seed', 'feedback', 'pre_run_quiet_seconds', 'post_run_capture_seconds'})
    run = table(raw, 'run', {'corpus_out', 'results_dir', 'logs_dir'}, {'results_dir'})
    version = telemetry.get('schema_version', 1)
    if type(version) is not int or version != 1:
        raise ValueError('telemetry.schema_version должен быть 1')
    if telemetry['mode'] != 'uart':
        raise ValueError('telemetry.mode должен быть uart')
    if campaign.get('feedback', 'none') != 'none' or corpus.get('strategy', 'round-robin') != 'round-robin':
        raise ValueError('Первый UART-профиль допускает только feedback=none и strategy=round-robin; KCOV не подменяется логами')
    udc = text(gadget['udc'], 'gadget.udc')
    driver = text(gadget.get('udc_driver', ''), 'gadget.udc_driver', empty=True)
    pattern = r'[A-Za-z0-9._@:-]{1,127}'
    if re.fullmatch(pattern, udc) is None or driver and re.fullmatch(pattern, driver) is None:
        raise ValueError('Некорректное имя UDC или драйвера')
    port = text(serial['port'], 'uart.port')
    if not port.startswith('/dev/') or port.endswith('/') or '..' in Path(port).parts or '*' in port or '?' in port:
        raise ValueError('uart.port должен быть точным абсолютным путём в /dev без обхода каталогов')
    parity = serial.get('parity', 'none')
    if parity not in ('none', 'even', 'odd'):
        raise ValueError('uart.parity: допустимы none, even, odd')
    settings = UARTSettings(port=port,
        baudrate=integer(serial.get('baudrate', 115200), 50, 4000000, 'uart.baudrate'),
        data_bits=integer(serial.get('data_bits', 8), 5, 8, 'uart.data_bits'), parity=parity,
        stop_bits=integer(serial.get('stop_bits', 1), 1, 2, 'uart.stop_bits'),
        read_timeout=number(serial.get('read_timeout', 0.25), 0, 5, 'uart.read_timeout', positive=True),
        reconnect_delay=number(serial.get('reconnect_delay', 1.0), 0.1, 60, 'uart.reconnect_delay'),
        read_chunk_bytes=integer(serial.get('read_chunk_bytes', 4096), 64, 65536, 'uart.read_chunk_bytes'))
    def relative(value, name):
        return (path.parent / text(value, name)).resolve()
    results = relative(run['results_dir'], 'run.results_dir')
    logs = relative(run.get('logs_dir', 'var/logs-uart'), 'run.logs_dir')
    prepared = relative(run.get('corpus_out', 'var/corpus-uart'), 'run.corpus_out')
    seeds = relative(corpus['seed_dir'], 'corpus.seed_dir')
    if len({results, logs, prepared, seeds}) != 4:
        raise ValueError('Каталоги seeds, подготовленного корпуса, результатов и логов должны различаться')
    return UARTLabConfig(path=path, udc=udc, udc_driver=driver,
        device_config=relative(gadget['device_config'], 'gadget.device_config'), seed_dir=seeds,
        manifest=relative(corpus['manifest'], 'corpus.manifest'), corpus_out=prepared,
        results_dir=results, logs_dir=logs, uart=settings,
        profile=text(gadget.get('profile', 'raspberry-pi-4'), 'gadget.profile', empty=True),
        families=names(corpus.get('families', []), 'corpus.families'),
        profiles=names(corpus.get('profiles', []), 'corpus.profiles'),
        iterations=integer(campaign.get('iterations', 100), 1, 1000000, 'campaign.iterations'),
        seconds=number(campaign.get('seconds', 2), 0, 300, 'campaign.seconds', positive=True),
        seed=integer(campaign.get('seed', 0), 0, 2**63-1, 'campaign.seed'),
        pre_run_quiet_seconds=number(campaign.get('pre_run_quiet_seconds', 0.5), 0, 30, 'campaign.pre_run_quiet_seconds'),
        post_run_capture_seconds=number(campaign.get('post_run_capture_seconds', 1.0), 0, 30, 'campaign.post_run_capture_seconds'))


def load(path):
    path = Path(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError('UART-конфигурация больше 64 КиБ')
    return from_dict(tomllib.loads(raw.decode('utf-8')), path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Проверка конфигурации UART без подключения к устройствам')
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
    except (OSError, ValueError) as error:
        parser.exit(2, 'Ошибка конфигурации: ' + str(error) + '\n')
    print(json.dumps({'configuration_valid': True, 'telemetry_mode': config.telemetry_mode,
                      'port': config.uart.port, 'baudrate': config.uart.baudrate,
                      'iterations': config.iterations, 'seconds': config.seconds, 'seed': config.seed,
                      'hardware_checked': False, 'runtime_connected': False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
