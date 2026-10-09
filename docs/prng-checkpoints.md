# PRNG checkpoint: формат и хранилище, этап 1

Этот коммит добавляет checkpoint.py и тесты. Он НЕ подключает запись checkpoint
к runner/control и НЕ меняет существующий resume. Кнопка resume пока по-прежнему
создаёт новую сессию. Подключение к циклу кампании — отдельный следующий коммит.

## Что хранится

- версия схемы 1 и runtime: implementation Python, полная версия major/minor/micro,
  random state version 3;
- identity стенда (64 hex), engine_id (64 hex) и campaign_id (32 hex);
- параметры iterations/seconds/seed/families/profiles;
- отсортированные уникальные digest корпуса (1..4096 элементов);
- next_iteration и last_run: граница после последней полностью завершённой попытки;
- независимые состояния random.Random выбора родителя и Mutator.random, включая
  MT state и Gaussian cache.

Формат — JSON с SHA-256 канонического содержимого, без pickle/eval. Контрольная
сумма обнаруживает повреждение, но НЕ аутентифицирует файл: её можно пересчитать.
Токены и учётные данные не должны попадать в переданные поля. Размер файла
ограничен 512 КиБ. Списки селекторов ограничены 128 именами по 127 символов.

## API

```python
value = capture_checkpoint(identity, engine_id, campaign_id, parameters, digests,
                           next_iteration, last_run, selection_rng, mutator.random)
save_checkpoint(path, value)
value = load_checkpoint(path, expected_identity=identity,
                        expected_engine_id=engine_id,
                        expected_parameters=parameters,
                        expected_corpus=digests)
selection_rng, mutation_rng = restore_generators(value)
mutator.random = mutation_rng
```

Перед сохранением capture нормализует seconds в float и сортирует корпус.
next_iteration допускается от 0 до iterations включительно. Для нулевой границы
last_run должен быть None, для остальных — ID завершённой попытки. Эти поля
валидируются структурно; модуль пока не проверяет их по runs.sqlite3.

restore_generators возвращает два НОВЫХ генератора после проверки всех полей:
он не меняет существующие генераторы при ошибке и не запускает устройства.
Проверяется схема, число/типы MT words, индекс, finite Gaussian cache и совместимость
runtime, identity, engine_id, параметров и корпуса. Дубли JSON и NaN/Infinity
отклоняются. Корпус не обрезается молча при превышении лимита.

Identity и engine_id обязан вычислять вызывающий код из актуального стенда и
всего кода, влияющего на выбор/мутации; модуль не вычисляет их автоматически.
expected_corpus должен отражать проверенное содержимое файлов, не только имена.
Их получение и проверка будет реализована при подключении к кампании.

## Файловые гарантии и границы

Родительский каталог должен уже существовать и быть доверенным. save использует
временный файл 0600, fsync файла, os.replace и fsync каталога. Ошибка до replace
оставляет предыдущий файл; ошибка fsync каталога после replace сообщается, но
новый файл уже может быть виден. Вызывающий код должен остановиться и проверить
состояние, а не считать любую ошибку доказательством сохранности старого файла.
load отвергает симлинк самого файла и нерегулярный тип; O_NONBLOCK не позволяет
зависнуть на FIFO. Родительские каталоги считаются доверенными. Нет встроенной
блокировки владельца: интеграция обязана использовать existing campaign.lock.

Checkpoint следует фиксировать только после Store.finish и сверять last_run с БД
при восстановлении. JSON и SQLite не одна транзакция. Разрыв между завершением
попытки и записью checkpoint требует отдельной reconciliation-политики, пока не
реализованной. Модуль не возобновляет незавершённую попытку и не сохраняет
WireMutator.streams внутри неё. Проверяется продолжение последовательности выбора,
мутаций и wire seed следующей конфигурации, а не идентичность трафика/таймингов USB.

## Проверки

```sh
python -m unittest discover -s tests -p 'test_checkpoint.py'
```

17 целевых тестов прошли: продолжение двух PRNG, Gaussian cache, реальные алгоритмы
Mutator (data/trace/wire seed), границы, совместимость, checksum, повреждённый JSON,
MT state, лимиты, атомарная запись, ошибки file/directory fsync и replace,
0600, симлинк/FIFO и отсутствие файла. Использована точная реализация mutator.py,
сверенная по Git blob f39ff6255fa717b35642b6b8e107400d94f438f8; DeviceConfig
был подставным. Полный репозиторий, campaign/Store/supervisor-интеграция и физический
стенд не проверены. Первый этап не означает, что resume уже поддерживает checkpoint.
