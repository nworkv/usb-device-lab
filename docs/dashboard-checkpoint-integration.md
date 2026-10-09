# Сквозные интеграционные тесты dashboard/checkpoint

Подготовлены 11 сценариев в tests/test_dashboard_checkpoint_integration.py.
Это тесты реального импортируемого пакета, а не реконструированного набора заглушек.
Production-код в этом коммите не меняется.

## Реальные компоненты и граница подмены

Используются настоящие DashboardServer, EventCampaignSupervisor и его persistent
родитель, Store, SQLite, DeviceConfig, Mutator, CorpusBrowser, checkpoint.py,
select_seeds/prepare_seeds, lab.load и classify/make_verdict/kernel_events.

Единственная подменяемая граница — оборудование: preflight, host orchestrator и
executor. Обёртка run_checkpoint_session вызывает оригинальную функцию с этими
тремя явными зависимостями; Store/модель/PRNG/корпус/identity/engine не заменяются.
Fixture не вызывает SSH, sudo, Raw Gadget или /dev/kmsg. Покрытие и KASAN-строки
синтетические: это проверка хранения/классификации, не проверка обнаружения багов
на реальном ядре. Токен фиксированный и используется только в локальном тесте.

## Сценарии

1. HTTP start/stop, закрытие сервера и supervisor, повторный запуск, HTTP resume;
   сравнение конфигураций/parent/trace с непрерывной кампанией и общего прогресса.
2. Настоящие results/corpus/logs/events API, авторизация, артефакты Store и snapshot.
3. Синтетический KASAN классифицируется unconfirmed kernel_candidate, не confirmed.
4. Изменение содержимого корпуса запрещает resume без нового execute.
5. Ошибка записи checkpoint после Store.finish оставляет gap, без авто-повтора.
6. Журнал событий сохраняется при перезапуске, автоматического запуска нет.
7. Исчерпанный бюджет не запускается снова через resume.
8. Повреждённая checksum запрещает resume.
9. Повреждённая campaign_attempts mapping не ремонтируется молча.
10. Running-запись сохраняется при отказе resume; Store.recover не вызывается.
11. Host/Origin проверки и read-only маршруты до первой кампании не создают runs DB.

Подключение через HTTP проверяется сервером, а не браузером. HTML/CSS визуально и
реальное выполнение JavaScript этим набором не проверяются. Эмуляция питания,
разрыв соединения USB, сообщения удалённого ядра и cleanup настоящих процессов
требуют отдельного стендового прогона.

## Запуск на полном checkout

```sh
python -m compileall -q usb_device_lab tests
make
python -m unittest discover -s tests -p 'test_dashboard_checkpoint_integration.py' -v
python -m unittest discover -s tests -v
```

Нужен Linux и Python 3.11+; Node.js нужен для имеющихся JS-тестов полного набора.
CI workflow .github/workflows/checkpoint-integration.yml использует Python 3.11/3.12,
Node.js 20, read-only contents permission, отдельный интеграционный прогон и затем
полный unittest discovery. Полный набор запускается с always(), чтобы его отчёт
был виден даже после падения интеграционных тестов; это не скрывает ошибку job.
Workflow новый, существующие workflows не перезаписываются. Он автоматически
запускается после push в feature/kernel-triage и на pull_request.

## Что действительно проверено при подготовке

Проверены компиляция Python-текста, наличие 11 unittest-сценариев и разбор YAML.
Интеграционные тесты здесь НЕ запускались: полного локального checkout со всеми
модулями не было, а выполнение на очередных подставных моделях не подтвердило бы
эту интеграцию. CI ещё НЕ запускался, зелёный статус не заявляется. Первый запуск
может выявить ошибки в production-коде или fixture; такие проблемы нужно исправить
отдельным коммитом, сохранив падающий тест как регрессию.

База подготовки: feature/kernel-triage, 1758995c5b976ac6ea7bddc799ea4a4384b46f1a.
Push/merge не выполнены. После отправки проверьте результаты ОБОИХ Python jobs,
число tests/skips, compile/build, полный набор и отсутствие зависших потоков.
Только после успешного CI переходите к ручной проверке физического стенда.
