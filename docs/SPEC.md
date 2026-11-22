# Цифровые Летописи природы — спецификация

Источник правды для контрактов (`contracts/`). Меняется только вместе с контрактами.

## Задача

Веб-сервис: летописи природы заповедника «Пасвик» (PDF-сканы, PDF с текстом, DOCX, XLSX)
→ структурированные записи наблюдений в PostgreSQL → поиск с фильтрами и временными
диапазонами, запрос обычными словами → таблица → выгрузка Excel/CSV → переход к исходной
странице с подсвеченным фрагментом → ручная правка и подтверждение записи.

Критерии жюри: качество извлечения, проверка (сверка с исходником, примеры ошибок,
неподдерживаемые документы), MVP, польза, обоснованность метода. Отсутствующие в тексте
значения не додумываются (null). Сомнительные значения помечаются флагами.

## Данные (data/raw, в git не кладём)

| Файл | PDF-стр. | Текстовый слой | Заметки |
|---|---|---|---|
| book02_1995.pdf | 66 | нет | каждая стр. PDF = разворот из 2 стр. книги; печатный номер внизу каждой половины; машинопись |
| book03_1996.pdf | 91 | нет | то же |
| book27_2020.pdf | 732 | есть на 728 | 610 стр. текста, 173 таблицы, 675 рисунков; таблицы в plain text идут столбиком; в таблицах даты за 2018–2020 |
| parameters_2020.xlsx | — | — | 6 листов, см. ниже |

Листы parameters_2020.xlsx:
- `шаблон_растения-1` — матрица «вид × номер фенофазы (1..9, 10а, 10б, 11, 12)», в ячейках даты.
- `шаблон_растения-2`, `шаблон_животные` — **целевой формат записи**, 13 колонок:
  Дата наблюдения · Год наблюдения · ФИО наблюдателя · Наименование вида (русский) ·
  Наименование вида (латынь) · Место регистрации (топоним) · Координаты встречи ·
  Местообитание · Характер явления, наблюдения · Численные показатели · Фенологическая фаза ·
  Ссылка на страницу/-цы в документе · Примечание.
  Пример животных: 31.03.1995 · 1995 · «Хохлов, Сметанников, Шершнев» · Заяц-беляк · — ·
  «Калкупя, южнее озера Каскамаярви» · — · «маршрут 1» · «регистрация следов на ЗМУ» · 7.5 ·
  «не фиксировалась» · «76-77» · «кол-во следов на 10 км маршрута».
  Пример растений: 04.08 · 2022(sic) · «Черанева В.Н., Бычков Ю.М, Крюкова С.А.» · Береза пушистая ·
  Betula pubescens · Мениккайоки · нет · «Березняк кустарничково-хвощовый» ·
  «фаза 12 - начало изменения окраски листьев» · нет · та же фаза · «109, том 1» · примечание.
- `примеры запросов` — 4 приёмочных запроса:
  1. Сводная таблица данных, которые зарегистрировали Макарова и Сметанников в 1995–1996.
  2. Все наблюдения за лосем в 1995, 1996 и 2022(=2020) годах по хронологии.
  3. Список всех публикаций о буром медведе за эти годы.
  4. Все регистрации зацветания герани лесной за эти годы.
- `каталог_сотрудники` — фамилия, год работы, год начала, год окончания (20 строк).
- `каталог_топонимы` — 37 топонимов с категорией (река/озеро/гора/…), **без координат**.

## Модель записи (observation)

| Группа | Поля |
|---|---|
| Тип | `record_type`: `observation` (наблюдение вида/явления), `env_measurement` (погода, вода, снег), `publication` (публикация из библиографии / раздела «Научные исследования») |
| Объект | `object_type` (taxon / phenomenon / env_indicator / publication), `object_raw` (как в тексте), `taxon_id?` |
| Время | `date_raw`, `date_from`, `date_to` (ISO), `date_precision` (day/decade/month/season/year/range), `obs_year` (год наблюдения; может отличаться от года книги) |
| Место | `toponym_raw`, `place_id?`, `coords_raw?`, `habitat_raw` |
| Событие | `event_raw` («характер явления»), `phenophase_id?`, `phenophase_raw` |
| Показатель | `indicator` (что измеряли), `value_num?`, `value_raw`, `unit` |
| Люди | `observers_raw` (как в тексте), `person_ids[]` |
| Публикация | `pub_authors`, `pub_year`, `pub_title`, `pub_venue` (только для record_type=publication) |
| Источник | `document_id`, `section_id`, `chunk_id`, `locator` (JSON, см. ниже), `printed_pages` (строка как в шаблоне: «76-77»), `quote` (дословно), `quote_span` [start,end] в тексте chunk, `bboxes` [[x0,y0,x1,y1] в долях страницы] |
| Прочее | `note` |
| Достоверность | `confidence` 0..1, `flags[]`, `status` (auto / verified / edited / rejected), `extractor_version` |

`locator`: для PDF `{"pdf_page": n, "half": "L"|"R"|null, "printed_page": "58"|null}`,
для DOCX `{"para": i}` или `{"table": t, "row": r}`, для XLSX `{"sheet": "...", "row": r}`.

Флаги (`flags`): `quote_not_found`, `value_not_in_quote`, `date_not_in_quote`, `date_ambiguous`,
`taxon_unmatched`, `taxon_ambiguous`, `place_unmatched`, `person_unmatched`,
`year_outside_book` (информативный), `low_ocr`, `table_parse_uncertain`.

## Справочники

- `taxa`: id, name_lat (принятое), name_ru, rank, genus, family, order, class, group_ru
  (сосудистые растения / мхи / лишайники / грибы / птицы / млекопитающие / рыбы /
  амфибии и рептилии / насекомые / прочие беспозвоночные), gbif_key.
- `taxon_names`: taxon_id, name, lang (ru/lat), kind (accepted/synonym/variant) — по ним работает
  поиск синонимов и названий рода.
- `places`: id, name, category, lat?, lon?, coords_source; `place_names`: варианты написания.
  Сид — каталог из xlsx.
- `persons`: id, surname, initials?, full_raw, years_active (из каталога сотрудников).
- `phenophases`: id, code ('1'..'9','10а','10б','11','12' для растений + коды для животных:
  прилёт, первая песня, гнездование, отлёт, выход из спячки и т.п.), name_ru, applies_to
  (plants/animals). Названия кодов растительной шкалы берутся из легенды в книге 2020
  (раздел 7.3 «Сезонная динамика растительных сообществ»); код без найденного названия → name_ru null.

## Таблицы

documents, chunks, sections, observations, observation_edits, taxa, taxon_names, places,
place_names, persons, observation_persons, phenophases, saved_queries.
Поиск: pg_trgm-индексы на taxon_names.name, place_names.name, persons.surname, object_raw.

## API (префикс /api)

| Метод | Путь | |
|---|---|---|
| GET | /health | |
| GET | /observations | фильтры: q (текст), taxon_id, taxon (строка: рус/лат/род), group, person (фамилия), place_id, place (строка), phenophase_id, indicator, record_type, document_id, date_from, date_to, years (через запятую), status, flagged; sort, order, page, page_size |
| POST | /query/parse | {text} → {filters, explanation} |
| GET | /observations/{id} | запись + chunk (текст) + section + edits |
| PATCH | /observations/{id} | {fields, action: edit/verify/reject, editor} → пишет observation_edits |
| GET | /export | format=xlsx\|csv + те же фильтры; колонки из contracts/export_columns.json |
| GET | /phenology | taxon_id, phenophase_id?, years? → строки (phenophase, year, date_from, date_to, observation_id) |
| GET | /places/{id}/species | year?, group? → список видов с числом записей и координатами места |
| GET | /suggest/{taxa\|places\|persons\|phenophases} | q → [{id,label,sublabel}] |
| GET | /documents | |
| GET | /documents/{id}/file | оригинал |
| GET | /documents/{id}/pages/{n}.png | картинка страницы PDF (для сканов — вся страница-разворот) |
| POST | /documents | multipart file + year + title → document со status queued |
| GET/POST | /saved-queries | {name, filters} |
| DELETE | /saved-queries/{id} | |
| GET | /quality | метрики по эталону: по полям precision/recall/f1, n; примеры ошибок; список неподдерживаемых страниц |

## Экспорт

13 колонок шаблона в том же порядке и с теми же заголовками + «Источник (ссылка)» +
«Достоверность» (статус и флаги). «Ссылка на страницу» = printed_pages, «том» = номер книги.
