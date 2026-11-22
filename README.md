# Цифровые Летописи природы

Сервис хакатона для научных сотрудников заповедника «Пасвик». Превращает летописи природы в структурированные наблюдения, измерения среды и публикации. Специалист ищет обычными словами или фильтрами, сверяет запись с подсвеченным фрагментом оригинала, исправляет её и выгружает Excel/CSV. Известные таблицы разбираются правилами, свободный текст — LLM Yandex AI Studio, сканы — через Yandex Vision OCR.

**Стенд:** http://51.250.9.93:8000/ — VM Yandex Cloud, Ubuntu 24.04; доступен из РФ, через некоторые VPN не открывается. По проверке из задания на 22.11.2026: 6278 записей книги 2020 и 5 тестовых записей 1995; OCR книг 1995/1996 ещё выполняется. Ограничения и сценарий показа — в [DEMO](docs/DEMO.md).

## Быстрый старт

Нужны Docker с Compose v2 и BuildKit. Из корня репозитория:

```sh
cd infra
docker compose up -d --build --wait
docker compose exec -T backend python pipeline/load.py
curl -fsS http://localhost:8000/api/health
```

Откройте http://localhost:8000/. Это база из фикстур (38 записей), без облачных ключей; поиск обычными словами работает по правилам. Docker собирает фронтенд через `npm ci`, если нет готового `frontend/dist`. Образец настроек — [infra/.env.example](infra/.env.example), копия для Compose — `infra/.env`.

Для оригиналов положите книги в `data/raw/` и из `infra/` выполните:

```sh
docker compose cp ../data/raw/. backend:/app/data/raw/
```

Без PDF таблица доступна, но страница-источник не откроется. Полная база из `release/`, VM и вариант без доступа к npm — в [DEPLOY](docs/DEPLOY.md).

## Структура

| Каталог | Назначение |
|---|---|
| [contracts](contracts/README.md) | JSON Schema, SQL, OpenAPI, экспорт и фикстуры; единый контракт |
| [pipeline](pipeline/) | Чтение документов, OCR, разделы, извлечение, цитаты, нормализация, оценка, загрузка |
| [backend](backend/) | FastAPI, PostgreSQL, поиск, правки, экспорт и оригиналы |
| [frontend](frontend/README.md) | React / TypeScript, поиск, карточка источника, проверка и фенология |
| [dictionaries](dictionaries/) | Виды, синонимы, места, обогащение через GBIF и OSM |
| [gold](gold/) | Шаблон эталона и контрольная разметка |
| [release](release/README.md) | Сжатые результаты обработки 2020 и кэш LLM |
| [infra](infra/README.md) | Compose, nginx, Dockerfile, установка и обновление VM |
| [docs](docs/) | Устройство проекта, данные, запуск, метод и демонстрация |

Исходные `book02_1995.pdf`, `book03_1996.pdf`, `book27_2020.pdf` и `parameters_2020.xlsx` хранятся в `data/raw/`, в git их нет.

## Документация

- [ARCHITECTURE](docs/ARCHITECTURE.md) — компоненты, потоки, БД, API и решения.
- [DEPLOY](docs/DEPLOY.md) — локальный запуск, VM, данные и обновления.
- [DATA](docs/DATA.md) — источники, поля записи, справочники и качество.
- [DEMO](docs/DEMO.md) — показ жюри на 5–7 минут и запасной план.
- [METHOD](docs/METHOD.md) — OCR/NLP, цитаты, нормализация, флаги и оценка.
- [SPEC](docs/SPEC.md) — требования и модель проекта.
- [DESIGN](docs/DESIGN.md) — требования к интерфейсу; редизайн ещё в работе.
- [Задачи](docs/tasks/README.md) — распределение работ; планы не означают готовность функций.

## Проверки

Python 3.12, зависимости из корня. Тестам нужен отдельный PostgreSQL с UTF-8, локалью для кириллицы и ролью с `CREATEDB`: они создают и удаляют временные базы. Пример подходит к локальному Compose по умолчанию (Bash; в PowerShell переменные задаются через `$env:ИМЯ = 'значение'`).

```sh
python -m pip install -r backend/requirements-dev.txt -r pipeline/ingest/requirements.txt
export DATABASE_URL=postgresql+psycopg://letopisi:letopisi@127.0.0.1:5432/letopisi
export TEST_DATABASE_URL=postgresql://letopisi:letopisi@127.0.0.1:5432/letopisi
export LLM_BACKEND=mock
python contracts/validate.py
python -m pytest -q backend/tests pipeline/tests pipeline/ingest/tests dictionaries/scripts/tests
python -m ruff check --select F backend pipeline
```

Четыре эталонных запроса — фильтры и разбор текста, со сводкой:

```sh
python -m pytest -q -s backend/tests/search/test_search_acceptance.py
```

Эти тесты используют фикстуры и сохранённый снимок из 572 записей в [backend/tests/search/data](backend/tests/search/data/). Числа отличаются от полной базы стенда; без `TEST_DATABASE_URL` проверки поиска пропускаются. Сверка формулировок с XLSX и часть тестов ядра требуют исходных файлов в `data/raw/`.

Проверки фронтенда:

```sh
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```
