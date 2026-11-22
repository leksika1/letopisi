# Готовые результаты пайплайна

Чтобы не платить за повторный прогон LLM, результаты ядра лежат здесь в сжатом виде.

## book27_2020 — Летопись 2020 (document_id = 3)

Прогон 2026-11-22: вся книга, YandexGPT (`gpt://…/yandexgpt/latest`), промпт `p1`, `extractor_version = core-0.1+p1`.

- 6273 записи наблюдений, ни одна не отброшена при проверке схемы.
- Извлечено по стратегиям: LLM — 2286, правила для таблиц — 3900, подписи к рисункам — 87.
- 1031 запрос к LLM, 7 фрагментов пропущено из-за ошибок API.

| Файл | Что внутри | Распаковать в |
|---|---|---|
| `observations_3.jsonl.gz` | записи по контракту Observation | `data/observations/3.jsonl` |
| `observations_3.report.json.gz` | отчёт прогона: флаги, стратегии, токены | `data/observations/3.report.json` |
| `chunks_3.jsonl.gz` | фрагменты текста (источник цитат) | `data/chunks/3.jsonl` |
| `chunks_3.lines.jsonl.gz` | строки фрагментов с координатами (для подсветки) | `data/chunks/3.lines.jsonl` |
| `sections_3.json.gz`, `sections_3.authors.json.gz` | разделы книги и их авторы | `data/sections/` |
| `llm_cache.tar.gz` | кэш ответов LLM: повторный `pipeline.run` с тем же промптом бесплатен | `data/` (`tar -xzf llm_cache.tar.gz -C data`) |

Распаковка (Git Bash):

```
mkdir -p data/observations data/chunks data/sections
for f in release/book27_2020/*.jsonl.gz release/book27_2020/*.json.gz; do
  n=$(basename "$f" .gz); dir=${n%%_*}; gunzip -c "$f" > "data/$dir/${n#*_}"
done
tar -xzf release/book27_2020/llm_cache.tar.gz -C data
```

Загрузка в БД — `pipeline/load.py` (задача B-1): записи ссылаются на `taxon_id`, `place_id`, `person_ids` и `phenophase_id` из справочников `contracts/fixtures/`; `chunk_id` — на `data/chunks/3.jsonl`.
