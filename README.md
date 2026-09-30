# USB Device Lab — WIP

Закрытая экспериментальная разработка программируемого USB-устройства для Linux Raw Gadget. Репозиторий не является готовым фаззером и не проверен на физическом USB-стенде.

## Цель

Одной JSON-конфигурацией описывать объявляемые USB-дескрипторы и исполняемую топологию: configurations, interfaces, alternate settings, endpoint'ы и обработчик поведения. Фаззер должен сохранять конфигурацию, применять structural и wire mutations, собирать remote KCOV с Linux-хоста и добавлять тест в корпус только при валидном новом покрытии.

## Честные ограничения

Raw Gadget обеспечивает низкоуровневое управление USB gadget из userspace, но не реализует автоматически протоколы любого класса. Новый backend целится в USB 2.0 full/high speed и bulk/interrupt endpoints. SuperSpeed, isochronous, USB hubs, полная class/state-machine семантика и гарантия эмуляции любого физического устройства не заявляются. Реальная возможность включить endpoint зависит от UDC.

## План компонентов

- `usb_device_lab.topology`: исполняемая runtime topology и проверка конфигураций/alternate settings.
- `usb_device_lab.protocol`: stateful script handler, trusted plugins и optional reference-device proxy.
- `usb_device_lab.mutator`: мутации JSON-дескрипторов и wire responses.
- `usb_device_lab.raw_gadget`: executor и state machine для standard requests.
- `host/`: remote KCOV collector и host log agent.
- `corpus/`: SQLite corpus, errors и воспроизведение.
- `web/`: локальный read-only error UI.

Использовать только на выделенном тестовом стенде. Потеря SSH не является доказательством kernel panic; для жёстких падений понадобятся serial/netconsole/pstore.
