# USB Device Lab — WIP

Закрытый экспериментальный проект: программируемое USB 2.0 устройство на Linux Raw Gadget, структурные и сетевые (wire) мутации, сбор remote KCOV с Linux-хоста, корпус по новому покрытию и локальный интерфейс просмотра ошибок.

**Статус.** Модули проверены unit-тестами с подставными Raw Gadget, агентом и коллектором. Не проверялись: физический UDC, реальный USB-хост, инструментированное KCOV-ядро и сквозной запуск на железе. Не считайте проект готовым фаззером.

## Состав

| Модуль | Назначение |
|---|---|
| `model.py`, `topology.py` | JSON-конфигурация, SHA-256, проверка configurations / interfaces / alternate settings / endpoint'ов |
| `protocol.py` | stateful `Script`-протокол и доверенные плагины |
| `mutator.py` | `Mutator` (дескрипторы, ответы сценария) и `WireMutator` (ответы на EP0/IN) |
| `raw_io.py`, `device.py` | ioctl Raw Gadget и конечный автомат устройства (EP0, SET_CONFIGURATION, SET_INTERFACE, halt, endpoint-потоки) |
| `host/kcov_remote.c` | C-коллектор remote KCOV по номеру USB-шины |
| `host_agent.py`, `host_logs.py`, `rpc.py` | агент на хосте: коллектор, `/dev/kmsg`, JSON-lines по stdin/stdout |
| `runner.py`, `errors.py`, `storage.py` | кампания по SSH, классификация ошибок, SQLite и корпус |
| `web.py` | локальный read-only интерфейс (127.0.0.1) |

## Быстрая проверка без железа

```sh
make            # собирает build/kcov-remote
make test
python3 -m usb_device_lab validate examples/composite.json
python3 -m usb_device_lab mutate examples/composite.json mutated.json --seed 123
python3 -m usb_device_lab seed examples/composite.json --state state
```

Нужны Linux, Python >= 3.10, компилятор C и заголовки Linux UAPI. Сторонние Python-библиотеки не требуются.

## Стенд

- **Gadget-машина** запускает эмулятор и кампанию. Нужны `CONFIG_USB_RAW_GADGET`, свободный UDC и доступ к `/dev/raw-gadget`; `udc_driver` и `udc_device` в JSON должны совпадать с вашим UDC (`dummy_udc` в примере — виртуальный).
- **Тестируемый хост** подключён USB-кабелем. Нужны `CONFIG_KCOV=y`, `CONFIG_KCOV_INSTRUMENT_ALL=y`, `CONFIG_DEBUG_FS=y`, собранный `build/kcov-remote` и права на `/sys/kernel/debug/kcov` и `/dev/kmsg`. Используйте отдельную шину: remote-покрытие охватывает всю шину.
- Управление идёт по отдельному SSH-каналу с ключами и проверкой host key.

```sh
python3 -m usb_device_lab replay device.json      # один эмулятор, без покрытия; остановка Ctrl+C

python3 -m usb_device_lab fuzz --seeds device.json --host usb-host \
  --agent-command "sudo -n python3 -m usb_device_lab.host_agent --bus 1 --collector /opt/usb-device-lab/build/kcov-remote" \
  --output state --iterations 1000 --seconds 5 --seed 123

python3 -m usb_device_lab web --output state --port 8080    # http://127.0.0.1:8080
```

Для доступа с другого компьютера используйте SSH-туннель. Интерфейс без аутентификации и слушает только loopback.

## Формат JSON

- `descriptors` — байты ответов на GET_DESCRIPTOR (`type`, `index`, `wIndex`, `hex`). Они не исправляются и могут быть намеренно повреждены.
- `runtime.configurations` — исполняемая топология, независимая от `descriptors`: configurations, interfaces, alternate settings и bulk/interrupt endpoint'ы (7-байтовый `descriptor_hex`, `payload_hex`, `read_length`, `interval_ms`).
- `protocol` — `{"name":"script","rules":[...]}`. Правило: `event` (`control`/`in`/`out`), `state`, `address`, `match`, `prefix`, `capture`, `reply_hex`, `reply_var`, `echo`, `send`, `next_state`.
- `wire_mutator` — `{"seed":N,"probability":P}`; детерминированные изменения исходящих ответов.

## Корпус и ошибки

Перед подключением устройства сохраняется `config.json` и метаданные запуска (родитель, мутации, seed). Конфигурация попадает в корпус, только если покрытие валидно, буфер не переполнен и есть новый PC в namespace `kernel release : boot ID : bus`. Ошибки сохраняются независимо от корпуса: kernel (`BUG`, `KASAN`, `UBSAN`, panic и др.), executor, потеря логов, насыщение KCOV, отсутствие покрытия, сбой агента. Пустое покрытие и сбой агента останавливают кампанию. Незавершённые запуски после рестарта получают `interrupted`; потеря SSH не считается доказательством kernel panic.

## Ограничения

- USB 2.0 full/high speed, bulk и interrupt endpoint'ы. Нет SuperSpeed, isochronous, хабов, streams и автоматической реализации классов (MSC, UVC, audio, сеть): их поведение нужно описать сценарием или плагином.
- Доступные endpoint'ы и скорость определяются UDC. Ioctl-константы Raw Gadget проверены по заголовкам Linux; другие архитектуры и ABI могут потребовать правок.
- Remote KCOV видит только аннотированные участки ядра.
- Kernel log читается best-effort (до 2 MiB на запуск). При жёстком падении хоста последние сообщения могут потеряться; serial / netconsole / pstore не интегрированы.
- Плагины протоколов — доверенный локальный код: `module:Class` должен быть перечислен в `USB_DEVICE_LAB_PLUGINS`.
- Используйте только на собственном изолированном стенде.

## Источники

- Raw Gadget: <https://docs.kernel.org/usb/raw-gadget.html>
- KCOV: <https://docs.kernel.org/dev-tools/kcov.html>
