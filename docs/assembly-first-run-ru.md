# Сборка стенда: короткий порядок первого запуска

Для Raspberry Pi 4 с Raw Gadget и выделенного разрешённого Linux-хоста. Это текущая ветка feature/uart-agentless: старый add-only патч повторно НЕ применять. Подробности соединений и SSH — в uart-ssh-full-setup-ru.md и pi-uart-setup-ru.md.

## До включения

- Независимый Ethernet/Wi-Fi к Pi должен работать до изменения gadget-конфигурации.
- Согласуйте питание и USB data-кабель: не соединяйте источники питания вслепую.
- UART: TX хоста -> RX приёмника, общий GND; сначала подтвердите уровни и распиновку. GPIO Pi — 3.3 V, не RS-232/5 V. Не подключайте неизвестный UART к GPIO.
- Используйте выделенный стенд и резервные копии: фаззинг может уронить ядро хоста.

## Checkout и gadget

Обычным пользователем, если каталога ещё нет:

```sh
git clone --branch feature/uart-agentless --single-branch https://github.com/nworkv/usb-device-lab.git "$HOME/usb-device-lab"
cd "$HOME/usb-device-lab"
git status --short
git rev-parse HEAD
```

Для существующего checkout сначала проверьте ветку и правки; обновляйте намеренно через git pull --ff-only. Не используйте reset --hard. Для приватного Git настройте доступ без токена в URL.

Boot/kernel готовятся вручную: backup, проверка конфликтов, подходящий профиль dwc2 peripheral. Установщик boot не меняет и Pi не перезагружает. После подготовки:

```sh
sudo modprobe dwc2
sudo modprobe raw_gadget
ls -1 /sys/class/udc
ls -l /dev/raw-gadget
```

Нет UDC/module — остановитесь. Не угадывайте имена и не занимайте UDC другим gadget. udc_driver — USB_UDC_NAME из /sys/class/udc/<UDC>/uevent, не Linux driver basename.

## Установка: выбрать один профиль

Из checkout, обычным оператором, НЕ sudo bash. Замените placeholders реальными значениями.

UART, при подтверждённой консоли хоста:

```sh
bash scripts/pi_dual_setup.sh --install-deps --mode uart --uart-port '/dev/serial/by-id/REAL_ADAPTER' --baud 115200 --iterations 3
```

115200 8N1 — пример. Либо SSH, с отдельным ключом, проверенным known_hosts и правом читать dmesg:

```sh
bash scripts/pi_dual_setup.sh --install-deps --mode ssh --ssh-host HOST_ADDRESS --ssh-user HOST_USER --ssh-key "$HOME/.ssh/usb_lab_ed25519" --known-hosts "$HOME/.ssh/usb_lab_known_hosts" --iterations 3
```

Не отключайте проверку host key. SSH-наблюдение и туннель оператора к Pi — разные соединения. SSH может потерять последние сообщения при падении ядра.

Скрипт выполняет compile, source-contract, UART-suite, cleanup gate, полный unittest-набор, JS syntax и metadata preflight. Ошибка останавливает установку — не обходите тесты. Cleanup-отчёт сохраняется в ~/.local/state/usb-device-lab/setup-logs/.

Существующий TOML сохраняется, generation-аргументы игнорируются: --iterations 3 НЕ меняет старый бюджет. Проверьте ~/.config/usb-device-lab/lab.dual.toml: для короткой приёмки campaign.iterations=3 и seconds=2. Другой конфиг задаётся абсолютным --config. Новый SSH-профиль имеет lease_seconds=0 — общего таймера capture нет; бюджет кампании при этом конечный.

## Первый запуск

```sh
PY="$HOME/usb-device-lab/.venv-uart/bin/python"
CFG="$HOME/.config/usb-device-lab/lab.dual.toml"
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" check
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" capture
```

Убедитесь в поступлении реальных сообщений хоста, затем Ctrl+C. Не запускайте несколько приёмников одного UART. Metadata check не подтверждает enumeration/полноту логов.

Только при готовом стенде и проверенном коротком бюджете:

```sh
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" run
```

Проверьте сохранение результатов и завершение USB executor/reader. При cleanup marker или незавершённом процессе остановитесь; маркер вслепую не удаляйте. Agentless UART/SSH используют feedback=none и не требуют KCOV.

Вместо параллельного CLI run можно запустить панель:

```sh
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" serve --port 8766
```

Панель доступна через loopback и SSH-туннель к Pi с одинаковым локальным/серверным портом 8766. Токен не публикуйте; logout не заменяет stop. Данные по умолчанию: ~/.local/state/usb-device-lab/.

## Статус

См. verification/package-1-software-verification.md и verification/package-2-assembly-handoff.md. Bash syntax проверена; установка на Pi и аппаратная приёмка НЕ выполнены. Полный make test, фактический CI и /dev/kmsg остаются открытыми. Это инструкция, не подтверждение готовности устройства.
