# Установка UART USB Lab на Raspberry Pi 4

## Перед установкой

Использовать выделенный разрешённый стенд. Подготовить питание/USB gadget и UART
консоль хоста вручную. На Pi 4 USB-C — power/OTG порт; схема питания и data-кабель
должны быть согласованы отдельно. Не соединять два источника питания вслепую.
UART GPIO Pi принимает 3.3 V, не 5 V/RS232. Подбирать адаптер по реальным уровням
хоста; TX хоста -> RX приёмника, GND общий. USB-UART подключается к USB Pi; GPIO
вариант использует /dev/serial0 после включения UART/освобождения от getty.
Консоль хоста должна действительно выводить сообщения ядра. Агент/KCOV не нужны.

Нужны Linux/Python 3.11+, Git-доступ к приватному nworkv/usb-device-lab и подходящее
ядро с Raw Gadget. Скрипт не делает OS/kernel upgrade, boot edit/reboot, rmmod,
chmod 777, снятие cleanup-marker или автоматический запуск USB-кампании.
Если нужен gadget boot-профиль, сначала backup и ручная проверка config.txt:
/boot/firmware/config.txt либо /boot/config.txt. Возможный профиль для Pi 4:

    [all]
    dtoverlay=dwc2,dr_mode=peripheral

Не добавлять вслепую поверх конфликтующих настроек. Не занимать UDC другим gadget.
Обеспечить независимый доступ к Pi до изменения USB-конфигурации. Нужен модуль
raw_gadget/CONFIG_USB_RAW_GADGET; ошибка modprobe не исправляется pip.

## Запуск

Запускать обычным оператором, НЕ sudo bash. Прочитать/доверять script/repo/venv:
панель в конце работает как root. Для updater безопаснее скопировать скрипт
вне checkout перед запуском, чтобы git pull не обновлял исполняемый файл:

```sh
cp scripts/pi_uart_setup.sh "$HOME/pi_uart_setup.sh"
bash "$HOME/pi_uart_setup.sh" \
  --uart-port '/dev/serial/by-id/ИМЯ_АДАПТЕРА' --baud 115200 \
  --install-deps --load-modules --serve
```

Путь и скорость заменить реальными. 115200 — пример; новый TOML использует 8N1,
если формат другой — изменить до аппаратного запуска. --install-deps разрешает
apt update/install, --load-modules разрешает modprobe только на определённой Pi 4.
Без флагов эти действия не выполняются. --serve открывает панель, НЕ фаззинг.
При SSH-доступе к GitHub добавить --repo-url 'git@github.com:nworkv/usb-device-lab.git'.
Не вставлять PAT в URL. Checkout должен быть на feature/uart-agentless без tracked
правок; скрипт не выбрасывает изменения/не переключает ветку за пользователя.

Скрипт: clone/ff-only update -> venv/pip -> Python compile -> source-contract ->
весь UART suite -> JS syntax -> UDC/driver -> TOML/manifest -> root metadata
preflight -> опционально foreground panel. Любая ошибка останавливает установку.
Node нужен для проверки JS. На Debian/Raspberry Pi OS apt ставит зависимости;
неподходящий Python/distro скрипт сам не обновляет.

## Конфигурация и панель

Конфиг: ~/.config/usb-device-lab/lab.uart.toml. Данные/логи:
~/.local/state/usb-device-lab/. Абсолютные пути, начальный HID keyboard, 3 попытки
по 2 секунды, feedback=none. --iterations меняет бюджет. Существующий совпадающий
конфиг сохраняется целиком; отличающийся не заменяется без --replace-config с backup.
Параметры --udc/--driver нужны при неоднозначной/нечитаемой автоматической выборке.

На Pi открыть http://127.0.0.1:8766 и ввести напечатанный token. Из-за tee token
может попасть и в setup-log; umask 077, но всё равно не публиковать его. Сервер
не выставлять в интернет. На компьютере использовать туннель (заменить YOUR_PI_USER,
адрес Pi актуализировать; ранее она была 10.10.10.215):

```sh
ssh -N -L 8766:127.0.0.1:8766 YOUR_PI_USER@10.10.10.215
```

Открыть localhost:8766 на компьютере. Локальный/серверный порты одинаковые из-за
Host-check. Это администрирование Pi, не агент тестируемого хоста.
Проверить настройки, подключение, UART консоль, затем нажать start. Preflight
не проверяет электрические уровни, скорость/консоль, tty identity или host health.

Повторный запуск без установки:

```sh
sudo -- "$HOME/usb-device-lab/.venv-uart/bin/python" \
  -m usb_device_lab.uart_guarded_web \
  --config "$HOME/.config/usb-device-lab/lab.uart.toml" --port 8766
```

Для установки/проверки без панели убрать --serve; для help использовать --help.
Отмена USB не прерывает уже начатый дисковый анализ; logout не останавливает кампанию.

## При ошибках

Read-only: ls -1 /sys/class/udc; ls -l /dev/raw-gadget; ls -l /dev/serial/by-id/.
Нет UDC/module — подготовить kernel/boot/controller вручную. Нет UART — проверить
адаптер/реальный device path/уровни. Не выбирать случайное имя. Cleanup marker —
проверить процесс и стенд, не удалять вслепую. Тесты упали — не обходить; сохранить
setup-logs/uart-tests-*.json и traceback. Удалить token/секреты перед отправкой логов.

## Проверенность

Шесть smoke-тестов прошли локально: Bash/help/вложенный Python и TOML генератор
с тестовым loader. В repo тесты используют настоящий uart_config.load. Полный
checkout CI, apt/Git/sudo/module install на Pi и аппаратный запуск здесь не
выполнены. Сам скрипт запускает программные проверки на вашей Pi и не объявляет
успех при их падении. Bash-файл запускается через bash; executable Git bit не нужен.

Источники: Raspberry Pi Python/venv documentation; официальная application note
Using OTG mode on Raspberry Pi SBCs; Linux kernel USB Raw Gadget documentation.
