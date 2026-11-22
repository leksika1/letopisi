# Запуск локально и на VM

## Локально

Из корня репозитория запустите PostgreSQL 16, FastAPI и nginx. Нужен Docker с Compose v2 и поддержкой BuildKit.

```sh
cd infra
docker compose up -d --build --wait
docker compose exec backend python pipeline/load.py
curl -fsS http://localhost:8000/api/health
```

Ожидаемый ответ через nginx — `{"status":"ok","db":true,"version":"1.0.0"}`. Повторная команда загрузки выводит по нулю новых строк для каждой таблицы. В исходных фикстурах 3 документа, 20 фрагментов, 38 наблюдений и 33 связи наблюдений с людьми.

Локальные настройки по умолчанию работают без ключей. Для собственных настроек скопируйте `.env.example` в `infra/.env`. PostgreSQL доступен с хоста по `127.0.0.1:5432` и хранит данные в томе `letopisi_pgdata`. Для остановки стека с сохранением базы выполните `docker compose down` из `infra/`.

При запуске backend применяет исходный `contracts/schema.sql` в транзакции. Схема включает расширение `pg_trgm`. При первом создании базы Compose задаёт UTF-8 и локаль `en_US.utf8` для поиска по кириллице.

Откройте приложение по адресу http://localhost:8000/. Если есть готовый `frontend/dist/index.html`, Docker включит сборку в образ nginx. При сборке из чистого репозитория Docker использует Node.js, выполняет `npm ci` и `npm run build`. Запросы к `/api` nginx передаёт в FastAPI. Поиск и экспорт используют один модуль фильтрации.

## Загрузка и проверки с хоста

После старта Compose выполните из корня репозитория в окружении Python 3.12:

```sh
python -m pip install -r backend/requirements-dev.txt -r pipeline/ingest/requirements.txt
python pipeline/load.py
python pipeline/load.py
python -m pytest -q backend/tests/test_service.py backend/tests/test_api.py pipeline/ingest/tests dictionaries/scripts/tests
python -m ruff check --select F backend pipeline
```

По умолчанию команды с хоста подключаются к `postgresql+psycopg://letopisi:letopisi@127.0.0.1:5432/letopisi`. При другом пароле или адресе задайте `DATABASE_URL` в окружении процесса. Внутри Compose имя хоста БД — `postgres`. Для интеграционных тестов роли нужно право `CREATEDB`. Каждый тест создаёт собственную временную базу и удаляет её после проверки.

Загрузчик предназначен для первичного заполнения базы фиксированными ID из контракта. Он читает массивы JSON и файлы JSONL, сохраняет исходные ID, ссылки на фрагменты и порядок наблюдателей. При повторном запуске пропускает уже существующие ID, сохраняя ручные правки. Для другого каталога с тем же набором файлов передайте путь в `python pipeline/load.py /path/to/fixtures`. Файл `quality.json` содержит пример ответа API.

Для полного прогона используйте `python -m pytest -q backend/tests pipeline/tests pipeline/ingest/tests dictionaries/scripts/tests`. Задайте `TEST_DATABASE_URL` для тестов поиска, укажите путь к снимку из 572 наблюдений в `SEARCH_SNAPSHOT_DIR`, если он хранится отдельно. Тесты ядра на страницах 449–450 используют исходную книгу и `pipeline/tests/data/llm_mock/responses.json`.

## Оригиналы, страницы и справочники

Поместите оригиналы книг в `data/raw/` под именами из `contracts/fixtures/documents.json`. В Compose оригиналы и PNG хранятся в томе `letopisi_documents`. После запуска из каталога `infra/` скопируйте документы и обработайте том 2020 года.

```sh
docker compose cp ../data/raw/. backend:/app/data/raw/
docker compose exec backend python -m pipeline.ingest data/raw/book27_2020.pdf --document-id 3
```

При запуске с хоста выполните ту же команду Python из корня репозитория. Загрузчик сохранит фрагменты и статистику в `data/chunks/`, PNG страниц в `data/pages/3/`. Если OCR недоступен, номера страниц останутся в `skipped_ocr`. Для распознавания задайте действующие `YC_API_KEY` и `YC_FOLDER_ID`, пересоздайте контейнер backend и повторите запуск. Ответы OCR сохраняются в `data/ocr_cache/`. Загрузка нового документа через API сохраняет оригинал и возвращает статус `queued`. Вызов фонового обработчика оставлен точкой подключения воркера.

Для обновления CSV из корня репозитория выполните:

```sh
python -m dictionaries.scripts.gbif_enrich
python -m dictionaries.scripts.geocode_places
```

`gbif_matches.csv` содержит тип и уверенность каждого совпадения GBIF. Записи для ручной сверки находятся в `review_taxa.csv` и `review_places.csv`. Поле `source` в справочнике координат ведёт на объект OpenStreetMap. Источники данных — [GBIF](https://www.gbif.org/) и [© OpenStreetMap contributors, ODbL](https://www.openstreetmap.org/copyright).

## На VM

На VM нужны Docker с Compose v2, SSH и rsync. SSH-пользователь должен иметь доступ к Docker и право создавать каталоги в своём домашнем каталоге. Скрипт запускается с машины с Bash, SSH и rsync. Откройте порт 8000 для пользователей сервиса. Порт PostgreSQL привязан к loopback.

1. По SSH создайте на VM каталог `~/letopisi`. Перед первым запуском сохраните там файл `~/letopisi/.env` по образцу `infra/.env.example`, ограничьте права командой `chmod 600 ~/letopisi/.env` и задайте собственный пароль в `POSTGRES_PASSWORD` и `DATABASE_URL`. В URL используйте хост `postgres` и URL-кодирование для специальных символов пароля.
2. Из корня локальной копии выполните команду ниже. Замените `user@vm` на SSH-адрес или алиас из конфигурации SSH.

```sh
bash infra/deploy.sh user@vm
```

Скрипт передаёт файлы сборки через rsync в новый каталог `~/letopisi/releases/`, затем выполняет на VM `docker compose up -d --build --wait`. Имя проекта Compose и том БД сохраняются при обновлениях. Первичную загрузку и проверку можно выполнить с локальной машины:

```sh
ssh user@vm 'cd ~/letopisi/current/infra && docker compose exec -T backend python pipeline/load.py'
ssh user@vm 'curl -fsS http://127.0.0.1:8000/api/health'
```
