# Цифровые Летописи природы

Веб-сервис для заповедника «Пасвик»: летописи природы (PDF-сканы, PDF с текстом, DOCX, XLSX)
превращаются в базу наблюдений с поиском, правкой, выгрузкой в Excel/CSV и переходом к исходной странице.

- Спецификация: [docs/SPEC.md](docs/SPEC.md)
- Контракты (схема записи, SQL, OpenAPI, фикстуры): [contracts/](contracts/README.md) — меняет только владелец ядра
- Проверка контрактов: `python contracts/validate.py`

## Данные

В git не хранятся. Положите файлы кейса в `data/raw/`:
`book02_1995.pdf`, `book03_1996.pdf`, `book27_2020.pdf`, `parameters_2020.xlsx`.

## Структура

```
contracts/   схема записи, schema.sql, openapi.yaml, фикстуры
pipeline/    ingest (Б), extract / normalize / eval (ядро), load.py (Б)
dictionaries/ скрипты и CSV справочников
backend/     FastAPI
frontend/    React
infra/       docker compose, nginx, deploy
gold/        эталонная разметка
```

Запуск появится после задач Б-1 и В-1.
