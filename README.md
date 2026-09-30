# USB Device Lab — WIP

Закрытый экспериментальный проект. Этот коммит содержит исполняемое базовое ядро, а не только архитектурный план.

Реализованы JSON DeviceConfig, runtime configurations/interfaces/alternate settings, stateful Script и trusted Protocol plugins, structural Mutator и WireMutator, SQLite corpus/errors и C-коллектор USB remote KCOV.

```sh
make
make test
python3 -m usb_device_lab validate examples/composite.json
python3 -m usb_device_lab mutate examples/composite.json mutated.json --seed 123
python3 -m usb_device_lab seed examples/composite.json --state state
```

Runtime topology проверяется независимо от объявляемых байтовых дескрипторов. Повреждённые USB поля намеренно разрешены. Корпус принимает конфигурацию только по новому валидному ненасыщенному покрытию в kernel/boot/bus namespace. Ошибки сохраняются независимо; незавершённые тесты отмечаются interrupted.

Коллектор собирается через make в build/kcov-remote, запускается на тестовом Linux-хосте с номером USB шины и принимает start/stop по stdin, возвращая JSON. Нужны инструментированное KCOV ядро и доступ к /sys/kernel/debug/kcov.

Raw Gadget executor, управляющий host-agent, связанный аппаратный цикл фаззинга и web UI в этот коммит ещё не входят. До их загрузки CLI не подключает USB-устройства. Не заявляются SuperSpeed, isochronous, hubs, автоматическая реализация всех USB-классов и обнаружение всех ошибок. Физический USB и реальный KCOV сбор не тестировались.

Использовать только на выделенном стенде. Plugins — доверенный локальный код; список module:Class задаётся USB_DEVICE_LAB_PLUGINS. Текущий backend scope: USB2 full/high speed, bulk/interrupt в пределах возможностей UDC.

Локальная проверка этого набора: 13 unit-тестов прошли, C-коллектор собрался с -Wall -Wextra -Werror, CLI validate отработал успешно.
