"""Проверка контракта contracts/.

Запуск из корня репозитория или из contracts/:
    python contracts/validate.py

Что проверяется:
  1. observation.schema.json — корректная JSON Schema 2020-12.
  2. Все фикстуры валидны по своим $defs (с проверкой форматов date/date-time).
  3. Ссылочная целостность (taxon_id, place_id, person_ids, document_id, chunk_id, section_id,
     phenophase_id, observation_id, ссылки в saved_queries и quality) и уникальность id.
  4. quote дословно содержится в text своего chunk, quote_span указывает на неё; флаги согласованы с данными.
  5. Согласованность между файлами: поля JSON Schema = колонки таблиц schema.sql; enum = CHECK в SQL;
     export_columns.json = заголовки шаблона из data/raw/parameters_2020.xlsx (если файл есть); gold/template.csv.
  6. schema.sql: синтаксис (pglast, если установлен) и применение в PostgreSQL (если доступен psql):
     создаётся временная БД, схема применяется дважды (идемпотентность), загружаются все фикстуры, БД удаляется.
     Подключение — стандартные переменные libpq (PGHOST, PGPORT, PGUSER, PGPASSWORD); путь к psql — PSQL или PATH.
  7. openapi.yaml парсится, все $ref (внутренние и в observation.schema.json) разрешаются, есть все пути из SPEC,
     примеры в openapi валидны по схемам.

Код выхода: 0 — всё прошло (пропущенные шаги допускаются), 1 — есть ошибки.
Зависимости: jsonschema, pyyaml (pip install jsonschema pyyaml); необязательно: pglast, openpyxl.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import jsonschema
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIX = HERE / "fixtures"

errors: list[str] = []
warnings: list[str] = []
skipped: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"  ОШИБКА: {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  предупреждение: {msg}")


def step(title: str) -> None:
    print(f"\n== {title}")


def load_json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def load_jsonl(name: str):
    rows = []
    for i, line in enumerate((FIX / name).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                err(f"{name}:{i}: невалидный JSON: {e}")
    return rows


# ---------------------------------------------------------------------------
step("1. JSON Schema")
SCHEMA = json.loads((HERE / "observation.schema.json").read_text(encoding="utf-8"))
try:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    print("  observation.schema.json — корректная схема draft 2020-12")
except jsonschema.SchemaError as e:
    err(f"observation.schema.json: {e.message}")
DEFS = SCHEMA["$defs"]
FORMAT_CHECKER = jsonschema.Draft202012Validator.FORMAT_CHECKER


def validator_for(defname: str) -> jsonschema.Draft202012Validator:
    sub = {"$schema": SCHEMA["$schema"], "$defs": DEFS, "$ref": f"#/$defs/{defname}"}
    return jsonschema.Draft202012Validator(sub, format_checker=FORMAT_CHECKER)


# ---------------------------------------------------------------------------
step("2. Фикстуры по схеме")
FILES = {
    "documents.json": "Document",
    "sections.json": "Section",
    "chunks.jsonl": "Chunk",
    "taxa.json": "Taxon",
    "taxon_names.json": "TaxonName",
    "places.json": "Place",
    "place_names.json": "PlaceName",
    "persons.json": "Person",
    "phenophases.json": "Phenophase",
    "observations.jsonl": "Observation",
    "observation_edits.jsonl": "ObservationEdit",
    "saved_queries.json": "SavedQuery",
}
data: dict[str, list] = {}
for fname, defname in FILES.items():
    path = FIX / fname
    if not path.exists():
        err(f"нет файла fixtures/{fname}")
        data[fname] = []
        continue
    rows = load_jsonl(fname) if fname.endswith(".jsonl") else load_json(fname)
    if not isinstance(rows, list):
        err(f"{fname}: ожидается массив")
        rows = []
    data[fname] = rows
    v = validator_for(defname)
    n_bad = 0
    for i, row in enumerate(rows):
        for e in sorted(v.iter_errors(row), key=lambda e: list(e.absolute_path)):
            n_bad += 1
            loc = "/".join(map(str, e.absolute_path))
            err(f"{fname}[{i}] id={row.get('id') if isinstance(row, dict) else '?'} {loc}: {e.message[:300]}")
    print(f"  {fname}: {len(rows)} шт., ошибок схемы: {n_bad}")

if (FIX / "quality.json").exists():
    q = load_json("quality.json")
    bad = list(validator_for("Quality").iter_errors(q))
    for e in bad:
        err(f"quality.json {'/'.join(map(str, e.absolute_path))}: {e.message[:300]}")
    print(f"  quality.json: ошибок схемы: {len(bad)}")
    data["quality.json"] = q
else:
    err("нет файла fixtures/quality.json")
    data["quality.json"] = None

export_cols = json.loads((HERE / "export_columns.json").read_text(encoding="utf-8"))
v = validator_for("ExportColumn")
for i, c in enumerate(export_cols):
    for e in v.iter_errors(c):
        err(f"export_columns.json[{i}]: {e.message}")
print(f"  export_columns.json: {len(export_cols)} колонок")

# ---------------------------------------------------------------------------
step("3. Ссылочная целостность")


def index(fname: str) -> dict:
    idx = {}
    for row in data.get(fname, []):
        rid = row.get("id")
        if rid in idx:
            err(f"{fname}: повторяющийся id {rid}")
        idx[rid] = row
    return idx


docs, secs, chunks = index("documents.json"), index("sections.json"), index("chunks.jsonl")
taxa, places, persons = index("taxa.json"), index("places.json"), index("persons.json")
phen, obs, edits = index("phenophases.json"), index("observations.jsonl"), index("observation_edits.jsonl")
index("taxon_names.json"), index("place_names.json"), index("saved_queries.json")


def ref(where: str, value, target: dict, tname: str) -> None:
    if value is not None and value not in target:
        err(f"{where}: {tname} {value} не существует")


for s in secs.values():
    ref(f"sections id={s['id']}", s["document_id"], docs, "document_id")
    ref(f"sections id={s['id']}", s["parent_id"], secs, "parent_id")
    if s["parent_id"] in secs and secs[s["parent_id"]]["document_id"] != s["document_id"]:
        err(f"sections id={s['id']}: родитель из другого документа")
for c in chunks.values():
    ref(f"chunks id={c['id']}", c["document_id"], docs, "document_id")
    ref(f"chunks id={c['id']}", c["section_id"], secs, "section_id")
    if c["section_id"] in secs and secs[c["section_id"]]["document_id"] != c["document_id"]:
        err(f"chunks id={c['id']}: section_id из другого документа")
seen_ord = set()
for c in chunks.values():
    k = (c["document_id"], c["ord"])
    if k in seen_ord:
        err(f"chunks id={c['id']}: повтор (document_id, ord) {k}")
    seen_ord.add(k)
for n in data["taxon_names.json"]:
    ref(f"taxon_names id={n['id']}", n["taxon_id"], taxa, "taxon_id")
for n in data["place_names.json"]:
    ref(f"place_names id={n['id']}", n["place_id"], places, "place_id")
for t in taxa.values():
    if not any(n["taxon_id"] == t["id"] and n["kind"] == "accepted" and n["lang"] == "lat" for n in data["taxon_names.json"]):
        err(f"taxa id={t['id']}: нет accepted-имени (lat) в taxon_names")

for o in obs.values():
    w = f"observations id={o['id']}"
    ref(w, o["document_id"], docs, "document_id")
    ref(w, o["section_id"], secs, "section_id")
    ref(w, o["chunk_id"], chunks, "chunk_id")
    ref(w, o["taxon_id"], taxa, "taxon_id")
    ref(w, o["place_id"], places, "place_id")
    ref(w, o["phenophase_id"], phen, "phenophase_id")
    for pid in o["person_ids"]:
        ref(w, pid, persons, "person_id")
    ch = chunks.get(o["chunk_id"])
    if ch and ch["document_id"] != o["document_id"]:
        err(f"{w}: chunk {ch['id']} из другого документа")
    if ch and o["section_id"] != ch["section_id"]:
        warn(f"{w}: section_id ({o['section_id']}) отличается от section_id chunk ({ch['section_id']})")
for e in edits.values():
    ref(f"observation_edits id={e['id']}", e["observation_id"], obs, "observation_id")

FILTER_REFS = {"taxon_id": taxa, "place_id": places, "phenophase_id": phen, "document_id": docs}
for sq in data["saved_queries.json"]:
    for k, target in FILTER_REFS.items():
        if k in sq["filters"]:
            ref(f"saved_queries id={sq['id']}", sq["filters"][k], target, k)
q = data.get("quality.json")
if q:
    for ex in q["error_examples"]:
        ref("quality.error_examples", ex["document_id"], docs, "document_id")
        ref("quality.error_examples", ex["observation_id"], obs, "observation_id")
    for u in q["unsupported"]:
        ref("quality.unsupported", u["document_id"], docs, "document_id")
    for b in q["by_document"]:
        ref("quality.by_document", b["document_id"], docs, "document_id")
print(f"  проверено: {len(obs)} записей, {len(chunks)} фрагментов, {len(edits)} правок")

# ---------------------------------------------------------------------------
step("4. Цитаты, флаги и логика записей")
n_quote_ok = 0
for o in obs.values():
    w = f"observations id={o['id']}"
    ch = chunks.get(o["chunk_id"])
    doc = docs.get(o["document_id"])
    flags = set(o["flags"])
    quote, span = o["quote"], o["quote_span"]
    if quote is not None and ch is None:
        err(f"{w}: есть quote, но нет chunk")
    if ch is not None and quote is not None:
        text = ch["text"]
        if "quote_not_found" in flags:
            if quote in text:
                err(f"{w}: флаг quote_not_found, но цитата есть в chunk {ch['id']}")
            if span is not None or o["bboxes"]:
                err(f"{w}: при quote_not_found quote_span должен быть null, bboxes пустым")
        else:
            if quote not in text:
                err(f"{w}: quote не найдена в chunk {ch['id']}: {quote[:80]!r}")
            elif span is None:
                err(f"{w}: нет quote_span")
            else:
                s, e = span
                if text[s:e] != quote:
                    err(f"{w}: quote_span {span} не указывает на quote (там {text[s:e][:60]!r})")
                else:
                    n_quote_ok += 1
    if span is not None and quote is None:
        err(f"{w}: quote_span без quote")
    if ch is not None and o["locator"] != ch["locator"]:
        if o["locator"].get("pdf_page") != ch["locator"].get("pdf_page"):
            err(f"{w}: locator.pdf_page не совпадает с locator chunk")
    # bboxes
    for b in o["bboxes"]:
        if not (b[0] <= b[2] and b[1] <= b[3]):
            err(f"{w}: bbox {b}: x0>x1 или y0>y1")
    if ch is not None and ch["text_source"] == "text_layer" and quote and "quote_not_found" not in flags and not o["bboxes"]:
        warn(f"{w}: цитата из текстового слоя без bboxes")
    # locator vs документ
    pdf_page = o["locator"].get("pdf_page")
    if doc and pdf_page is not None and doc["pages"] is not None and pdf_page > doc["pages"]:
        err(f"{w}: pdf_page {pdf_page} > числа страниц документа {doc['pages']}")
    if doc and doc["layout"] == "single" and o["locator"].get("half") is not None:
        err(f"{w}: half задан для документа без разворотов")
    # даты
    df, dt = o["date_from"], o["date_to"]
    if df and dt and df > dt:
        err(f"{w}: date_from > date_to")
    if (df or dt) and o["date_precision"] is None:
        err(f"{w}: есть дата, но date_precision = null")
    if o["date_precision"] is not None and not (df or dt or o["date_raw"]):
        err(f"{w}: date_precision={o['date_precision']} без даты (date_raw, date_from, date_to пусты)")
    if o["date_precision"] == "day" and df != dt:
        err(f"{w}: date_precision=day, но date_from ≠ date_to")
    if df and o["obs_year"] is not None and int(df[:4]) != o["obs_year"] and o["record_type"] != "publication":
        err(f"{w}: obs_year {o['obs_year']} не совпадает с годом date_from {df}")
    # год книги
    if doc and o["obs_year"] is not None and doc["year"] is not None:
        outside = o["obs_year"] != doc["year"]
        if outside and "year_outside_book" not in flags:
            err(f"{w}: obs_year {o['obs_year']} ≠ году книги {doc['year']}, нет флага year_outside_book")
        if not outside and "year_outside_book" in flags:
            err(f"{w}: флаг year_outside_book, но obs_year = году книги")
    # публикации
    pub_fields = ["pub_authors", "pub_year", "pub_title", "pub_venue"]
    if o["record_type"] == "publication":
        if o["object_type"] != "publication":
            err(f"{w}: publication с object_type={o['object_type']}")
        if not o["pub_title"]:
            err(f"{w}: у публикации нет pub_title")
    elif any(o[f] is not None for f in pub_fields):
        err(f"{w}: pub_* заполнены у record_type={o['record_type']}")
    if o["object_type"] == "taxon" and o["taxon_id"] is None and not flags & {"taxon_unmatched", "taxon_ambiguous"}:
        err(f"{w}: object_type=taxon без taxon_id и без флага taxon_unmatched/taxon_ambiguous")
    if o["toponym_raw"] and o["place_id"] is None and "place_unmatched" not in flags:
        warn(f"{w}: toponym_raw без place_id и без флага place_unmatched")
    # значение в цитате
    if o["value_num"] is not None and quote and "value_not_in_quote" not in flags and "quote_not_found" not in flags:
        cands = {str(o["value_num"]), str(o["value_num"]).replace(".", ","), (o["value_raw"] or "\0")}
        if float(o["value_num"]).is_integer():
            cands.add(str(int(o["value_num"])))
        if not any(c in quote for c in cands):
            warn(f"{w}: value {o['value_num']} / value_raw {o['value_raw']!r} не видно в цитате")
    if "value_not_in_quote" in flags and quote and o["value_raw"] and o["value_raw"] in quote:
        err(f"{w}: флаг value_not_in_quote, но value_raw есть в цитате")
    # статус и журнал правок
    own_edits = sorted((e for e in edits.values() if e["observation_id"] == o["id"]), key=lambda e: e["created_at"])
    if o["status"] != "auto" and not own_edits:
        err(f"{w}: status={o['status']}, но нет записей в observation_edits")
    if own_edits and own_edits[-1]["status_after"] != o["status"]:
        err(f"{w}: status={o['status']} ≠ status_after последней правки ({own_edits[-1]['status_after']})")
    for e in own_edits:
        expected = {"edit": "edited", "verify": "verified", "reject": "rejected"}[e["action"]]
        if e["status_after"] != expected:
            err(f"observation_edits id={e['id']}: action={e['action']} → status_after должен быть {expected}")
        for field, ch_ in e["changes"].items():
            if field in o and o[field] != ch_["new"] and e is own_edits[-1]:
                err(f"observation_edits id={e['id']}: поле {field} у записи ({o[field]!r}) ≠ new ({ch_['new']!r})")
print(f"  цитаты, найденные дословно по quote_span: {n_quote_ok} из {sum(1 for o in obs.values() if o['quote'])}")

for c in chunks.values():
    if c["text_source"] == "ocr" and c["ocr_confidence"] is None:
        warn(f"chunks id={c['id']}: OCR без ocr_confidence")

# ---------------------------------------------------------------------------
step("5. Согласованность JSON Schema ↔ schema.sql ↔ экспорт")
SQL = (HERE / "schema.sql").read_text(encoding="utf-8")
SQL_NOCOMMENT = re.sub(r"--[^\n]*", "", SQL)


def sql_tables(sql: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        out[m.group(1)] = m.group(2)
    return out


def sql_columns(body: str) -> list[str]:
    cols = []
    depth = 0
    cur = ""
    for chx in body:
        if chx == "(":
            depth += 1
        elif chx == ")":
            depth -= 1
        if chx == "," and depth == 0:
            cols.append(cur)
            cur = ""
        else:
            cur += chx
    cols.append(cur)
    names = []
    for c in cols:
        c = c.strip()
        if not c or re.match(r"(CHECK|UNIQUE|PRIMARY KEY|FOREIGN KEY|CONSTRAINT)\b", c):
            continue
        names.append(c.split()[0].strip('"'))
    return names


tables = sql_tables(SQL_NOCOMMENT)
EXPECTED_TABLES = ["documents", "chunks", "sections", "observations", "observation_edits", "taxa", "taxon_names",
                   "places", "place_names", "persons", "observation_persons", "phenophases", "saved_queries"]
for t in EXPECTED_TABLES:
    if t not in tables:
        err(f"schema.sql: нет таблицы {t}")
TABLE_DEF = {"documents": "Document", "sections": "Section", "chunks": "Chunk", "taxa": "Taxon",
             "taxon_names": "TaxonName", "places": "Place", "place_names": "PlaceName", "persons": "Person",
             "phenophases": "Phenophase", "observations": "ObservationCore", "observation_edits": "ObservationEdit",
             "saved_queries": "SavedQuery"}
VIRTUAL = {"observations": {"person_ids"}}  # поля API, которых нет колонкой (person_ids → observation_persons)
for t, d in TABLE_DEF.items():
    if t not in tables:
        continue
    cols = set(sql_columns(tables[t]))
    props = set(DEFS[d]["properties"]) - VIRTUAL.get(t, set())
    if cols - props:
        err(f"{t}: колонки без поля в {d}: {sorted(cols - props)}")
    if props - cols:
        err(f"{t}: поля {d} без колонки: {sorted(props - cols)}")
print(f"  таблиц: {len(tables)}, сверено колонок с JSON Schema: {len(TABLE_DEF)} таблиц")


def sql_in_list(pattern: str) -> set[str] | None:
    m = re.search(pattern, SQL_NOCOMMENT, re.S)
    if not m:
        return None
    return set(re.findall(r"'([^']*)'", m.group(1)))


ENUMS = [
    ("observations.record_type", r"record_type\s+text[^,]*?CHECK \(record_type IN \(([^)]*)\)", DEFS["RecordType"]["enum"]),
    ("observations.object_type", r"object_type\s+text[^,]*?CHECK \(object_type IN \(([^)]*)\)", DEFS["ObjectType"]["enum"]),
    ("observations.date_precision", r"date_precision\s+text\s+CHECK \(date_precision IN \(([^)]*)\)", [x for x in DEFS["DatePrecision"]["enum"] if x]),
    ("observations.status", r"status\s+text\s+NOT NULL DEFAULT 'auto' CHECK \(status IN \(([^)]*)\)", DEFS["Status"]["enum"]),
    ("documents.status", r"CHECK \(status IN \(('queued'[^)]*)\)", DEFS["DocumentStatus"]["enum"]),
    ("documents.format", r"CHECK \(format IN \(([^)]*)\)", DEFS["DocumentFormat"]["enum"]),
    ("chunks.kind", r"CHECK \(kind IN \(('page'[^)]*)\)", DEFS["ChunkKind"]["enum"]),
    ("chunks.text_source", r"CHECK \(text_source IN \(([^)]*)\)", DEFS["TextSource"]["enum"]),
    ("taxa.group_ru", r"CHECK \(group_ru IN \((.*?)\)\)", DEFS["TaxonGroup"]["enum"]),
    ("taxa.rank", r"CHECK \(rank IN \(([^)]*)\)", DEFS["Taxon"]["properties"]["rank"]["enum"]),
    ("places.coords_source", r"CHECK \(coords_source IN \(([^)]*)\)", [x for x in DEFS["CoordsSource"]["enum"] if x]),
    ("phenophases.applies_to", r"CHECK \(applies_to IN \(([^)]*)\)", DEFS["Phenophase"]["properties"]["applies_to"]["enum"]),
    ("observation_edits.action", r"CHECK \(action IN \(([^)]*)\)", DEFS["ObservationEdit"]["properties"]["action"]["enum"]),
]
for name, pat, expected in ENUMS:
    got = sql_in_list(pat)
    if got is None:
        err(f"schema.sql: не найден CHECK для {name}")
    elif got != set(expected):
        err(f"{name}: SQL {sorted(got)} ≠ JSON Schema {sorted(expected)}")
m = re.search(r"flags <@ '(\[.*?\])'::jsonb", SQL_NOCOMMENT, re.S)
if not m or set(json.loads(m.group(1))) != set(DEFS["Flag"]["enum"]):
    err("observations.flags: список в SQL ≠ Flag enum в JSON Schema")
print(f"  enum ↔ CHECK: сверено {len(ENUMS) + 1}")

editable = set(DEFS["ObservationEditableFields"]["properties"])
if editable != set(DEFS["EditableFieldName"]["enum"]):
    err("ObservationEditableFields.properties ≠ EditableFieldName.enum")
if editable - set(DEFS["ObservationCore"]["properties"]):
    err(f"редактируемые поля без поля записи: {editable - set(DEFS['ObservationCore']['properties'])}")

# export columns
headers = [c["header"] for c in export_cols]
if len(headers) != 15 or headers[13:] != ["Источник (ссылка)", "Достоверность"]:
    err(f"export_columns.json: ожидается 13 + «Источник (ссылка)» + «Достоверность», есть {len(headers)}")
xlsx = ROOT / "data" / "raw" / "parameters_2020.xlsx"
try:
    import openpyxl  # noqa
    have_openpyxl = True
except ImportError:
    have_openpyxl = False
if xlsx.exists() and have_openpyxl:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    for sh in ("шаблон_животные", "шаблон_растения-2"):
        row = next(r for r in wb[sh].iter_rows(values_only=True) if r and r[0] == "Дата наблюдения")
        tpl = [str(x).strip().replace("фааза", "фаза") for x in row[:13]]
        if tpl != headers[:13]:
            err(f"export_columns.json: заголовки ≠ листу {sh}: {tpl}")
    print("  export_columns.json: 13 заголовков совпадают с листами шаблон_животные и шаблон_растения-2")
else:
    skipped.append("сверка заголовков с parameters_2020.xlsx (нет файла или openpyxl)")
    print("  пропуск: сверка с parameters_2020.xlsx (нет data/raw/parameters_2020.xlsx или openpyxl)")

gold = ROOT / "gold" / "template.csv"
if gold.exists():
    raw = gold.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        err("gold/template.csv: нет BOM UTF-8")
    hdr = next(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
    if hdr != headers[:13] + ["PDF-страница", "Документ", "Цитата"]:
        err(f"gold/template.csv: заголовок не совпадает: {hdr}")
    else:
        print("  gold/template.csv: заголовок верный (BOM, «;»)")
else:
    err("нет gold/template.csv")

# ---------------------------------------------------------------------------
step("6. schema.sql в PostgreSQL")
try:
    import pglast
    try:
        pglast.parse_sql(SQL)
        print(f"  синтаксис корректен (pglast {pglast.__version__})")
    except Exception as e:  # pglast.parser.ParseError
        err(f"schema.sql: синтаксическая ошибка: {e}")
except ImportError:
    skipped.append("синтаксическая проверка pglast (не установлен)")
    print("  пропуск синтаксической проверки: pglast не установлен (pip install pglast)")

PSQL = os.environ.get("PSQL") or shutil.which("psql")


def psql(args: list[str], sql: str | None = None, db: str = "postgres") -> subprocess.CompletedProcess:
    cmd = [PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db] + args
    env = dict(os.environ, PGCLIENTENCODING="UTF8")
    return subprocess.run(cmd, input=sql.encode("utf-8") if sql is not None else None,
                          capture_output=True, env=env, timeout=120)


def fixtures_sql() -> str:
    """INSERT всех фикстур через jsonb_populate_recordset — колонки берутся по именам полей JSON."""
    order = [("documents", "documents.json"), ("sections", "sections.json"), ("chunks", "chunks.jsonl"),
             ("taxa", "taxa.json"), ("taxon_names", "taxon_names.json"), ("places", "places.json"),
             ("place_names", "place_names.json"), ("persons", "persons.json"), ("phenophases", "phenophases.json"),
             ("observations", "observations.jsonl"), ("observation_edits", "observation_edits.jsonl"),
             ("saved_queries", "saved_queries.json")]
    parts = ["BEGIN;"]
    for table, fname in order:
        rows = data[fname]
        if table == "sections":  # родители раньше детей
            rows = sorted(rows, key=lambda r: (r["parent_id"] is not None, r["id"]))
        payload = json.dumps(rows, ensure_ascii=False)
        assert "$fx$" not in payload
        parts.append(f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, $fx${payload}$fx$::jsonb);")
    links = [{"observation_id": o["id"], "person_id": p, "ord": i}
             for o in data["observations.jsonl"] for i, p in enumerate(o["person_ids"])]
    parts.append("INSERT INTO observation_persons SELECT * FROM jsonb_populate_recordset(NULL::observation_persons, "
                 f"$fx${json.dumps(links, ensure_ascii=False)}$fx$::jsonb);")
    for table, _ in order:
        parts.append(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}));")
    parts.append("SELECT count(*) FROM observations WHERE object_raw % 'лось' OR taxon_id IS NOT NULL;")
    parts.append("COMMIT;")
    return "\n".join(parts)


if not PSQL:
    skipped.append("применение schema.sql (psql не найден)")
    print("  пропуск: psql не найден (укажите путь в переменной PSQL или добавьте в PATH)")
else:
    probe = psql(["-tAc", "SELECT 1"])
    if probe.returncode != 0:
        skipped.append("применение schema.sql (нет подключения к PostgreSQL)")
        print("  пропуск: нет подключения к PostgreSQL через psql "
              f"({probe.stderr.decode('utf-8', 'replace').strip()[:200]}); задайте PGHOST/PGPORT/PGUSER/PGPASSWORD")
    else:
        ver = psql(["-tAc", "SHOW server_version"]).stdout.decode().strip()
        tmpdb = f"letopisi_validate_{uuid.uuid4().hex[:8]}"
        # Подбираем LC_CTYPE, при котором pg_trgm видит кириллицу (при C — не видит).
        r = None
        for loc in ["", "C.UTF-8", "en_US.UTF-8", "ru_RU.UTF-8", "en-US", "ru-RU"]:
            opts = f" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE '{loc}' LC_CTYPE '{loc}'" if loc else ""
            psql(["-c", f'DROP DATABASE IF EXISTS "{tmpdb}"'])
            r = psql(["-c", f'CREATE DATABASE "{tmpdb}"{opts}'])
            if r.returncode != 0:
                continue
            t = psql(["-tA"], sql="CREATE EXTENSION IF NOT EXISTS pg_trgm; SELECT show_trgm('лось') <> '{}'::text[];", db=tmpdb)
            if t.stdout.decode().strip().endswith("t"):
                print(f"  временная БД: LC_CTYPE = {loc or 'по умолчанию кластера'} (pg_trgm видит кириллицу)")
                break
        else:
            warn("не нашлось LC_CTYPE, при котором pg_trgm видит кириллицу; проверка trgm-поиска будет неточной")
        if r is None or r.returncode != 0:
            skipped.append("применение schema.sql (нет прав на CREATE DATABASE)")
            print(f"  пропуск: не удалось создать временную БД: {r.stderr.decode('utf-8', 'replace')[:200]}")
        else:
            try:
                schema_path = str(HERE / "schema.sql")
                r = psql(["-f", schema_path, "-f", schema_path], db=tmpdb)
                if r.returncode != 0:
                    err(f"schema.sql не применяется: {r.stderr.decode('utf-8', 'replace')[:800]}")
                else:
                    print(f"  schema.sql применена дважды без ошибок (PostgreSQL {ver})")
                    r = psql(["-tA"], sql=fixtures_sql(), db=tmpdb)
                    if r.returncode != 0:
                        err(f"фикстуры не загружаются в БД: {r.stderr.decode('utf-8', 'replace')[:800]}")
                    else:
                        # SQL через stdin: кириллица в аргументах командной строки ломается в Windows
                        cnt = psql(["-tA"], sql="SELECT (SELECT count(*) FROM observations), "
                                   "(SELECT count(*) FROM observation_persons), (SELECT count(*) FROM chunks), "
                                   "(SELECT count(*) FROM taxon_names WHERE name % 'Лось');", db=tmpdb)
                        got = cnt.stdout.decode("utf-8", "replace").strip().split("|")
                        n_expected = len(data["observations.jsonl"])
                        if len(got) == 4 and got[3] == "0" and not any("pg_trgm" in w_ for w_ in warnings):
                            err("pg_trgm: поиск «Лось» по taxon_names ничего не нашёл")
                        if cnt.returncode != 0 or len(got) != 4 or int(got[0]) != n_expected:
                            err(f"после загрузки в БД не то число записей: {got} (ожидалось {n_expected}); "
                                f"{cnt.stderr.decode('utf-8', 'replace')[:300]}")
                        else:
                            print(f"  все фикстуры загружены (FK и CHECK выполнены): записей {got[0]}, "
                                  f"связей с людьми {got[1]}, фрагментов {got[2]}; pg_trgm-поиск «Лось» → {got[3]} имён")
            finally:
                psql(["-c", f'DROP DATABASE IF EXISTS "{tmpdb}"'])

# ---------------------------------------------------------------------------
step("7. openapi.yaml")
API = yaml.safe_load((HERE / "openapi.yaml").read_text(encoding="utf-8"))
if not str(API.get("openapi", "")).startswith("3.1"):
    err(f"openapi.yaml: версия {API.get('openapi')} (нужна 3.1.x)")
docs_cache = {"observation.schema.json": SCHEMA, "": API}


def resolve(ref_: str, base: str):
    file, _, pointer = ref_.partition("#")
    file = file.lstrip("./") if file else base
    if file not in docs_cache:
        p = HERE / file
        if not p.exists():
            raise KeyError(f"файл {file} не найден")
        docs_cache[file] = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else yaml.safe_load(p.read_text(encoding="utf-8"))
    node = docs_cache[file]
    for part in [p for p in pointer.split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        elif part in node:
            node = node[part]
        else:
            raise KeyError(f"{file}#{pointer}: нет '{part}'")
    return node, file


n_refs = 0


def walk(node, base: str, seen: set):
    global n_refs
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            key = (base, node["$ref"])
            n_refs += 1
            try:
                target, tfile = resolve(node["$ref"], base)
            except (KeyError, ValueError, IndexError) as e:
                err(f"$ref {node['$ref']} (в {base or 'openapi.yaml'}): {e}")
                target = None
            if target is not None and key not in seen:
                seen.add(key)
                walk(target, tfile, seen)
        for k, v_ in node.items():
            if k != "$ref":
                walk(v_, base, seen)
    elif isinstance(node, list):
        for v_ in node:
            walk(v_, base, seen)


walk(API, "", set())
print(f"  openapi {API.get('openapi')}: разрешено ссылок $ref: {n_refs}")

SPEC_PATHS = {
    ("get", "/health"), ("get", "/observations"), ("post", "/query/parse"), ("get", "/observations/{id}"),
    ("patch", "/observations/{id}"), ("get", "/export"), ("get", "/phenology"), ("get", "/places/{id}/species"),
    ("get", "/suggest/{kind}"), ("get", "/documents"), ("get", "/documents/{id}/file"),
    ("get", "/documents/{id}/pages/{n}.png"), ("post", "/documents"), ("get", "/saved-queries"),
    ("post", "/saved-queries"), ("delete", "/saved-queries/{id}"), ("get", "/quality"),
}
have = {(m, p) for p, ops in API.get("paths", {}).items() for m in ops if m in {"get", "post", "patch", "delete", "put"}}
for m, p in sorted(SPEC_PATHS - have):
    err(f"openapi.yaml: нет {m.upper()} {p}")
print(f"  эндпоинтов: {len(have)}, из SPEC отсутствуют: {len(SPEC_PATHS - have)}")

# параметры фильтров = поля SearchFilters
list_params = {resolve(p["$ref"], "")[0]["name"] for p in API["paths"]["/observations"]["get"]["parameters"]}
sf = set(DEFS["SearchFilters"]["properties"])
if sf - list_params:
    err(f"GET /observations: нет параметров для полей SearchFilters: {sorted(sf - list_params)}")
if list_params - sf - {"page", "page_size"}:
    err(f"GET /observations: параметры без поля в SearchFilters: {sorted(list_params - sf - {'page', 'page_size'})}")
export_params = {resolve(p["$ref"], "")[0]["name"] if "$ref" in p else p["name"] for p in API["paths"]["/export"]["get"]["parameters"]}
if (list_params - {"page", "page_size"}) - export_params:
    err(f"GET /export: не хватает фильтров {sorted((list_params - {'page', 'page_size'}) - export_params)}")

# примеры в openapi
ex_checks = [
    (API["paths"]["/query/parse"]["post"]["responses"]["200"]["content"]["application/json"]["example"], "QueryParseResponse"),
    (API["paths"]["/query/parse"]["post"]["requestBody"]["content"]["application/json"]["example"], "QueryParseRequest"),
]
for name, ex in API["paths"]["/observations/{id}"]["patch"]["requestBody"]["content"]["application/json"]["examples"].items():
    ex_checks.append((ex["value"], "ObservationPatch"))
for value, d in ex_checks:
    for e in validator_for(d).iter_errors(value):
        err(f"openapi.yaml: пример {d} невалиден: {e.message}")
ex_tid = API["paths"]["/query/parse"]["post"]["responses"]["200"]["content"]["application/json"]["example"]["filters"]["taxon_id"]
if taxa.get(ex_tid, {}).get("name_lat") != "Alces alces":
    err("openapi.yaml: в примере /query/parse taxon_id не указывает на лося в фикстурах")
print(f"  примеров проверено: {len(ex_checks)}")

# ---------------------------------------------------------------------------
print("\n== Итог")
print(f"  ошибок: {len(errors)}, предупреждений: {len(warnings)}, пропущено шагов: {len(skipped)}")
for s in skipped:
    print(f"  пропущено: {s}")
if errors:
    print("  РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ")
    sys.exit(1)
print("  РЕЗУЛЬТАТ: OK")
