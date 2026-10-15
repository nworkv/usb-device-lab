# USB Lab: полный запуск с выбором UART или SSH

## Что реализовано

Патч предназначен для nworkv/usb-device-lab, feature/uart-agentless,
база d6b62b5ec81c308362b59dc8dc3c259daabc4376. Добавлены новые модули,
старые UART/SSH-agent/RPC входы не изменены. Новый вход: dual_cli.
Изменения можно отправить одним логически полным коммитом.

Выбирается один источник наблюдения для каждой кампании:
- UART: существующий приёмник консоли, без агента и без передачи команд по UART.
- SSH: стандартный клиент OpenSSH на Pi и временные стандартные команды Linux
  на хосте; Python/наш агент на хост не устанавливаются.

Управление start/stop/resume выполняется на Pi через одну панель/CLI в обоих
режимах. USB всегда исполняется на Pi. SSH сюда означает Pi -> тестируемый
хост; SSH-туннель оператор -> Pi — другое независимое соединение.
Это не реализация автоматического serial login, reboot или питания хоста.
Одновременное смешивание UART и SSH в одной кампании намеренно не реализовано.

## Безопасность и пределы

Только выделенный разрешённый стенд, backup данных и отдельная сеть управления.
USB-фаззинг может повесить/уронить ядро. Raw Gadget — отладочный интерфейс.
UART: подтвердите электрические уровни, TX -> RX, общий GND, скорость/формат.
GPIO Pi — 3.3 V, не RS-232 и не 5 V. USB-C Pi 4 — gadget/data-порт; питание
согласовать отдельно. Не подключайте два источника питания вслепую.

SSH не заменяет аппаратный UART при падении ядра: при потере сети поток может
исчезнуть раньше последних сообщений. Разрыв/выход потока останавливает попытки;
он не классифицируется как kernel panic. Не включён скрытый переход SSH -> UART.
Часы — monotonic времени приёма на Pi, не время события на хосте. completeness
всегда unknown; KCOV отсутствует, feedback=none, кандидаты не доказывают причинность.

SSH использует ключ, BatchMode, StrictHostKeyChecking=yes, отдельный known_hosts,
нечитает пользовательский ssh config, не делает port/agent forwarding. Паролей
и произвольной команды в TOML/UI нет. Пути ключей/known_hosts — абсолютные,
без пробелов, % и ~. Для root-запуска заранее проверьте доступность ключа.
Приватный ключ должен быть обычным файлом, доступным процессу, без group/other
доступа; known_hosts нельзя делать group/world-writable. Доверенные родительские
каталоги и checkout — ответственность оператора: панель работает с root-правами.

SSH readiness означает успешный пробный доступ к dmesg и запуск stream-процесса,
не доказанную полноту/здоровье хоста. stdout — dmesg escaped text, не байтовая
копия /dev/kmsg. stderr отдельно ограничен последними 16 KiB.

На хосте запускается фиксированный shell wrapper, dmesg, cat и timeout. Перед
USB проверяются поддержка --follow-new и доступ к чтению. EOF клиентского stdin
просит wrapper завершить reader. Remote timeout ограничивает lease (600 s по
умолчанию, 30..3600 s); при истечении кампания останавливается. Это best-effort
cleanup: после сетевого сбоя/blocked kernel/проблемы прав проверьте remote-процессы,
не считайте их отсутствие доказанным. Автоматического повторного подключения нет.
Для долгих кампаний заранее подберите lease или делайте совместимый resume;
изменение lease/SSH-настроек меняет fingerprint и не подходит для старого resume.

## 1. Получить checkout и применить патч

На Pi, обычным пользователем, при Python >=3.11:

```sh
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip nodejs openssh-client

git clone --branch feature/uart-agentless --single-branch   https://github.com/nworkv/usb-device-lab.git "$HOME/usb-device-lab"
cd "$HOME/usb-device-lab"
git status --short
git rev-parse HEAD
```

Если каталог уже существует, не удаляйте его. Проверьте ветку/локальные изменения
и сделайте намеренный git pull --ff-only. Для приватного HTTPS GitHub потребуется
PAT вместо пароля, либо используйте настроенный SSH URL. Не пишите PAT в URL.

Перенесите приложенный .patch на Pi как $HOME/usb-dual-transport.patch. Архив
исходников предназначен для чтения; не нужно распаковывать его поверх уже
применённого патча. Проверка и применение не затрагивают boot или железо:

```sh
cd "$HOME/usb-device-lab"
git apply --check "$HOME/usb-dual-transport.patch"
git apply "$HOME/usb-dual-transport.patch"
```

Это add-only патч: он не перезаписывает существующие модули. Если имя нового
файла уже занято, git apply должен остановиться; не обходите проверку.

## 2. Подготовить gadget

Независимый Ethernet/Wi-Fi к Pi должен работать до изменения USB. В правильном
config.txt (/boot/firmware/config.txt или /boot/config.txt) после backup и проверки
конфликтов нужен профиль Pi 4:

```ini
[all]
dtoverlay=dwc2,dr_mode=peripheral
```

Boot-конфиг и reboot выполняются оператором, не установщиком. После загрузки:

```sh
sudo modprobe dwc2
sudo modprobe raw_gadget
ls -1 /sys/class/udc
ls -l /dev/raw-gadget
```

Не загружайте g_ether/g_serial для этого фаззера и не занимайте UDC другим gadget.
Если raw_gadget отсутствует, нужен соответствующий kernel/module, не pip-пакет.
Нельзя подставлять вымышленный UDC. Новый init читает имя/драйвер из sysfs либо
принимает явно --udc/--driver; эти значения проверяются preflight.

## 3A. Подготовить UART

USB-адаптер подключить к USB-A Pi, serial-сторону — к консоли хоста с правильными
уровнями. Найдите реальное устройство:

```sh
ls -l /dev/serial/by-id/ 2>/dev/null
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
sudo dmesg | tail -n 50
```

Для GPIO UART освободите порт от serial getty/собственной консоли Pi и включите
UART hardware; только затем выбирайте /dev/serial0. Хост должен выводить сообщения
ядра в свой serial-порт. Для Linux пример console=ttyS0,115200n8 применим только
если ttyS0 действительно нужный UART; конфигурация загрузчика зависит от хоста.
115200 8N1 — пример, не автоопределение. Не угадывайте уровни/распиновку.

## 3B. Подготовить SSH к тестируемому хосту

На тестируемом Linux должны быть настроены OpenSSH server, dmesg с --follow-new,
sh, cat, grep, sleep и GNU timeout. Сеть управления не должна зависеть от USB,
который фаззится. Пользователь SSH должен читать kernel log. По умолчанию
use_sudo=true требует sudo -n без пароля для ровно нужных read-only команд.

Подготовьте отдельный ключ штатным способом и авторизуйте public key на хосте.
Не передавайте/не публикуйте private key. Получите host-key fingerprint по
независимому доверенному каналу и вручную подтвердите первый SSH-вход. ssh-keyscan
сам по себе не доказывает личность хоста.

Например (YOUR_HOST_USER и пути заменить):

```sh
ssh -i "$HOME/.ssh/usb_lab_ed25519"   -o UserKnownHostsFile="$HOME/.ssh/usb_lab_known_hosts"   YOUR_HOST_USER@10.10.10.208 true
```

Если нужен sudo, администратор может через visudo разрешить только чтение:

```text
YOUR_HOST_USER ALL=(root) NOPASSWD: /usr/bin/dmesg --raw, /usr/bin/dmesg --follow-new --raw
```

Уточните реальный путь command -v dmesg. Не разрешайте NOPASSWD: ALL и не
отключайте глобально dmesg_restrict ради установщика. При уже разрешённом чтении
без sudo задайте --ssh-direct (use_sudo=false).

Проверьте тем же UID, под которым будет работать панель:

```sh
sudo -- ssh -F /dev/null -T   -i "$HOME/.ssh/usb_lab_ed25519"   -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes   -o UserKnownHostsFile="$HOME/.ssh/usb_lab_known_hosts"   -o GlobalKnownHostsFile=/dev/null   YOUR_HOST_USER@10.10.10.208 'sudo -n dmesg --raw >/dev/null'
```

Если ключ зашифрован и недоступен неинтерактивно, эта конфигурация не готова.
Патч не хранит пароль ключа и не открывает agent forwarding. Администратор сам
выбирает приемлемый для стенда способ доступа; не удаляйте passphrase вслепую.

## 4. Установка/генерация конфига

Из checkout, обычным пользователем, НЕ sudo bash. Новый установщик не делает
Git update, modprobe, boot edit, reboot, chmod 777 или USB запуск. Он устанавливает
venv/ПО, проверяет новый suite/JS/source-contract, генерирует TOML и делает root
metadata preflight. --install-deps отдельно разрешает apt. --serve запускает
только сайт. Существующий конфиг не заменяется, generation-аргументы игнорируются.

Один профиль UART:

```sh
bash scripts/pi_dual_setup.sh --install-deps   --mode uart --uart-port '/dev/serial/by-id/REAL_ADAPTER' --baud 115200 --serve
```

Один профиль SSH:

```sh
bash scripts/pi_dual_setup.sh --install-deps   --mode ssh --ssh-host 10.10.10.208 --ssh-user YOUR_HOST_USER   --ssh-key "$HOME/.ssh/usb_lab_ed25519"   --known-hosts "$HOME/.ssh/usb_lab_known_hosts" --serve
```

Оба профиля, чтобы переключать их в сайте:

```sh
bash scripts/pi_dual_setup.sh --install-deps   --mode uart --uart-port '/dev/serial/by-id/REAL_ADAPTER' --baud 115200   --ssh-host 10.10.10.208 --ssh-user YOUR_HOST_USER   --ssh-key "$HOME/.ssh/usb_lab_ed25519"   --known-hosts "$HOME/.ssh/usb_lab_known_hosts" --serve
```

--iterations задаёт начальный бюджет; по умолчанию 3 попытки по 2 s, HID keyboard.
При существующем конфиге отредактируйте его намеренно или создайте другой через
--config. Не ожидайте изменения режима от generation-аргументов при сохранённом
файле. Контроль режима при повторном запуске выполняет dual_cli --mode.

Конфиг: ~/.config/usb-device-lab/lab.dual.toml. Данные:
~/.local/state/usb-device-lab/. В новом TOML сохраняются только заданные профили;
для выбора отсутствующего профиля заполните [uart] или [ssh]. Пример обоих:
examples/lab.dual.example.toml (CHANGE_ME обязательно заменить).

## 5. Открыть сайт

На Pi панель слушает только http://127.0.0.1:8766 и печатает токен. Не публикуйте
токен/terminal output. Запуск держать в устойчивой сессии (например tmux) или
подготовленном service; закрытие SSH-терминала не является корректной остановкой.
Не устанавливается systemd service автоматически, не открываются firewall/наружный порт.

На компьютере оператора во втором терминале:

```sh
ssh -N -L 8766:127.0.0.1:8766 YOUR_PI_USER@10.10.10.215
```

Откройте http://127.0.0.1:8766 на компьютере и введите токен. Локальный и серверный
порт одинаковые для Host-check. Через http://10.10.10.215:8766 прямого доступа нет.
В сайте выберите UART/SSH и нажмите «Выбрать режим». При активной кампании или
анализе переключение отклоняется. Выбор живёт в памяти; после перезапуска default
берётся из telemetry.mode TOML либо --mode. Текущий mode/target показан в state.
Выполните short acceptance, затем нажмите «Новая кампания».
«Остановить попытки» не прерывает уже начатый дисковый анализ; logout также не stop.

## 6. Повторный запуск и CLI

```sh
PY="$HOME/usb-device-lab/.venv-uart/bin/python"
CFG="$HOME/.config/usb-device-lab/lab.dual.toml"

sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode uart check
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode ssh check
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" serve --port 8766
```

Проверка capture без USB (Ctrl+C для завершения), по одному приёмнику за раз:

```sh
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode uart capture
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode ssh capture
```

Новая кампания (CLI и сайт не запускать параллельно):

```sh
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode uart run
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode ssh run
```

Resume, UUID заменить реально существующим:

```sh
sudo -- "$PY" -m usb_device_lab.dual_cli --config "$CFG" --mode ssh run   --resume "$HOME/.local/state/usb-device-lab/results/UUID/checkpoint.json"
```

Несовместимый режим/target/seed/бюджет/тайминги/SSH настройки отклоняются. Legacy
UART fingerprint сохранён, контейнер checkpoint по историческим причинам всё
ещё называется uart-round-robin-v1; SSH имеет отдельный mode/settings fingerprint.
Legacy SSH/KCOV checkpoint не поддерживается. При resume создаётся новый capture
UUID. Уже завершённую кампанию продолжать нельзя. При прерывании текущая попытка
может повториться; завершённый checkpoint-prefix не повторяется.

## 7. Результаты

results/UUID/campaign.json содержит реальный mode и capture_directory.
logs/uart/UUID и logs/ssh/UUID разделены. Имя uart.raw оставлено для совместимого
журнала/анализатора и в SSH capture; это не означает режим UART.
chunks.jsonl содержит Pi receive-time/byte offsets, events.jsonl — диагностику.
SSH stderr отдельно в ssh-stderr.log, не подмешивается в kernel candidates.

results/UUID/attempt-*/: config.json, started.json, window.json, result.json,
executor.log. Checkpoint обновляется только после сохранения завершённой попытки.
UDC блокируется общим lock; ненулевой cleanup marker не удаляется автоматически.

После stop capture выполняется существующий bounded evidence-анализ: pipeline-*
(summary/JSONL/SQLite) и evidence-* (части). Новый transport-analysis.json явно
отмечает mode, источник/часы/unknown completeness. JSON-файлы/журналы скачиваются
через allowlist с Bearer token, nofollow и лимитом 32 MiB (413 для больших).
Панель показывает JSON summary/attempts и ссылки; это не произвольный terminal
и не новый HTML-дизайнер. HTML-публикация не добавлена; оригинальные старые
UART report-инструменты сохранены. История максимум 200, details последние 100
attempts и 20 export-директорий; количество показанных элементов не полный итог.

## 8. Программные проверки на полном checkout

```sh
cd "$HOME/usb-device-lab"
PY="$HOME/usb-device-lab/.venv-uart/bin/python"
"$PY" -m compileall -q usb_device_lab tests scripts
bash -n scripts/pi_dual_setup.sh
"$PY" scripts/check_uart_checkpoint_source.py
"$PY" -m unittest discover -s tests -p 'test_dual_*.py' -v
"$PY" scripts/run_uart_tests.py --output /tmp/usb-lab-uart-regression.json
"$PY" - <<'PY'
import subprocess
from usb_device_lab.dual_ui import SCRIPT
subprocess.run(['node', '--check'], input=SCRIPT, check=True)
PY
```

Добавлен GitHub Actions workflow для Python 3.11/3.12. Он подготовлен, но не
запущен здесь. Нельзя считать наличие workflow подтверждением зелёного CI.

## 9. Аппаратная приёмка отдельно для обоих режимов

1. Capture-only даёт ожидаемые реальные сообщения хоста (не только connected).
2. Short campaign 3 попытки, подтверждена USB обработка хостом.
3. Проверены остановка, отсутствие оставшегося USB executor и SSH reader.
4. Stop/resume: завершённый prefix не повторяется; другой transport отклоняется.
5. Разрыв UART/SSH сохраняет диагностику и прекращает новые USB попытки.
6. Попытка переключить режим на ходу отклоняется.
7. Есть сырые logs/индекс/result/checkpoint и автоматический анализ; скачивание
   требует токен и работает через tunnel.
8. Отдельно проверены permissions, host-key mismatch и отказ remote dmesg.

Connected/preflight/UI completed не доказывают enumeration, отсутствие panic или
полноту захвата. Cleanup marker, receiver/thread/process not reaped — остановиться
и исследовать стенд; не очищать его вслепую.

## Что проверено при подготовке патча

15 isolated tests прошли с реальными local pipes/processes, файловыми журналами
и HTTP loopback. Legacy config/journal/checkpoint/helpers для этого прогона
восстановлены локально из прочитанных исходников (сокращены сообщения ошибок);
pipeline boundary, model/mutator/receiver для lifecycle тестов — fixtures/mocks.
Это НЕ прогон всего исходного checkout. Проверены compile, Bash/JS syntax и
применение add-only патча к временному Git-дереву. Не выполнены полный repository
regression, GitHub CI, настоящий SSH server/permissions, PTY/физический UART,
Raw Gadget/Pi install и аппаратный запуск. Перед эксплуатацией обязательны
полный checkout suite и аппаратная приёмка выше.
