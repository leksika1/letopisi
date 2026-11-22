"""Один параметризованный SQL поиска по observations.

Все значения — bind-параметры (``sqlalchemy.text`` + ``:name``); в текст SQL попадают только
имена колонок из белых списков. Массивы передаются через ``CAST(:p AS bigint[])`` — так работают
и psycopg 3, и psycopg2, в том числе с пустыми списками.

Семантика фильтров (contracts/README.md, «Решения», пп. 8–10):
- даты — пересечение интервалов: ``date_from записи ≤ :to`` и ``date_to записи ≥ :from``;
  записи без дат исключаются только при заданном диапазоне;
- ``years`` → ``obs_year``, ``doc_years`` → ``documents.year``;
- ``status`` не задан → без ``rejected``;
- ``flagged=true`` → есть флаги, ``false`` → флагов нет;
- строковые ``taxon`` / ``person`` / ``place`` → id справочников ИЛИ текстовый запасной вариант
  (см. resolve.py) для записей без id.

Сортировка: по полю из ``SortField``; ``date_from`` (и по умолчанию) — хронологически:
ключ ``COALESCE(date_from, 31.12.obs_year)`` — записи без даты стоят в конце своего года,
затем ``obs_year``, затем ``id``; NULL всегда в конце.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from sqlalchemy import text

from pipeline.normalize.text import fold, words

from . import resolve as R
from .filters import SearchFilters

OBS_COLUMNS = [
    "id", "record_type", "object_type", "object_raw", "taxon_id", "date_raw", "date_from", "date_to",
    "date_precision", "obs_year", "toponym_raw", "place_id", "coords_raw", "habitat_raw", "event_raw",
    "phenophase_id", "phenophase_raw", "indicator", "value_num", "value_raw", "unit", "observers_raw",
    "pub_authors", "pub_year", "pub_title", "pub_venue", "document_id", "section_id", "chunk_id", "locator",
    "printed_pages", "quote", "quote_span", "bboxes", "note", "confidence", "flags", "status",
    "extractor_version", "created_at", "updated_at",
]

FROM_SQL = """
FROM observations o
JOIN documents d ON d.id = o.document_id
LEFT JOIN taxa t ON t.id = o.taxon_id
LEFT JOIN places pl ON pl.id = o.place_id
LEFT JOIN phenophases ph ON ph.id = o.phenophase_id
LEFT JOIN sections s ON s.id = o.section_id
"""

SELECT_SQL = ",\n       ".join(f"o.{c}" for c in OBS_COLUMNS) + """,
       ARRAY(SELECT op.person_id FROM observation_persons op
             WHERE op.observation_id = o.id ORDER BY op.ord, op.person_id) AS person_ids,
       ARRAY(SELECT pr.surname || COALESCE(' ' || pr.initials, '')
             FROM observation_persons op JOIN persons pr ON pr.id = op.person_id
             WHERE op.observation_id = o.id ORDER BY op.ord, op.person_id) AS l_persons,
       t.name_ru AS l_taxon_name_ru, t.name_lat AS l_taxon_name_lat, t.group_ru AS l_taxon_group,
       pl.name AS l_place_name, ph.code AS l_phenophase_code, ph.name_ru AS l_phenophase_name,
       d.title AS l_document_title, d.book_no AS l_book_no, d.year AS l_book_year"""

CHRONO_KEY = "COALESCE(o.date_from, make_date(o.obs_year, 12, 31))"
SORT_EXPR = {
    "date_from": CHRONO_KEY,
    "obs_year": "o.obs_year",
    "object_raw": "lower(o.object_raw)",
    "toponym_raw": "lower(o.toponym_raw)",
    "confidence": "o.confidence",
    "status": "o.status",
    "id": "o.id",
    "created_at": "o.created_at",
    "updated_at": "o.updated_at",
}

F = R.sql_fold


@dataclass
class Where:
    """Накопитель условий WHERE и bind-параметров с автонумерацией имён."""

    clauses: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    _n: int = 0

    def p(self, value: Any) -> str:
        self._n += 1
        name = f"p{self._n}"
        self.params[name] = value
        return f":{name}"

    def add(self, clause: str) -> None:
        self.clauses.append(clause)

    def sql(self) -> str:
        return ("WHERE " + "\n  AND ".join(self.clauses)) if self.clauses else ""


@dataclass
class Prepared:
    """Разрешённые фильтры (id справочников + ARE) — для SQL и для пояснений."""

    filters: SearchFilters
    taxon_by_id: R.Resolved | None = None
    taxon: R.Resolved | None = None
    persons: R.Resolved | None = None
    place: R.Resolved | None = None
    place_by_id: list[str] | None = None


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def prepare(conn, filters: SearchFilters) -> Prepared:
    """Разрешить строковые фильтры через справочники в БД."""
    pr = Prepared(filters=filters)
    if filters.taxon_id is not None:
        pr.taxon_by_id = R.taxon_by_id(conn, filters.taxon_id)
    if filters.taxon:
        pr.taxon = R.resolve_taxon(conn, filters.taxon)
    if filters.person:
        pr.persons = R.resolve_persons(conn, filters.person)
    if filters.place:
        pr.place = R.resolve_place(conn, filters.place)
    if filters.place_id is not None:
        _p, pr.place_by_id = R.place_by_id(conn, filters.place_id)
    return pr


def _taxon_clause(w: Where, res: R.Resolved) -> str:
    parts = []
    if res.ids:
        parts.append(f"o.taxon_id = ANY(CAST({w.p(res.ids)} AS bigint[]))")
    rx = res.regex
    if rx:
        hay = F("o.object_raw || ' ' || COALESCE(o.pub_title, '')")
        parts.append(f"(o.taxon_id IS NULL AND {hay} ~ {w.p(rx)})")
    return "(" + " OR ".join(parts) + ")" if parts else "FALSE"


def build_where(pr: Prepared) -> Where:
    f = pr.filters
    w = Where()
    # статус
    if f.status:
        w.add(f"o.status = {w.p(f.status)}")
    else:
        w.add("o.status <> 'rejected'")
    if f.record_type:
        w.add(f"o.record_type = {w.p(f.record_type)}")
    if f.document_id is not None:
        w.add(f"o.document_id = {w.p(f.document_id)}")
    # вид
    if pr.taxon_by_id is not None:
        w.add(_taxon_clause(w, pr.taxon_by_id))
    if pr.taxon is not None:
        w.add(_taxon_clause(w, pr.taxon))
    if f.group:
        w.add(f"t.group_ru = {w.p(f.group)}")
    # люди
    if pr.persons is not None:
        res = pr.persons
        parts = []
        if res.ids:
            ids = w.p(res.ids)
            parts.append("EXISTS (SELECT 1 FROM observation_persons op WHERE op.observation_id = o.id "
                         f"AND op.person_id = ANY(CAST({ids} AS bigint[])))")
            parts.append(f"(o.observers_raw IS NULL AND s.author_person_ids && CAST({ids} AS bigint[]))")
        rx = res.regex
        if rx:
            prx = w.p(rx)
            parts.append(f"{F('o.observers_raw')} ~ {prx}")
            parts.append(f"(o.record_type = 'publication' AND {F('o.pub_authors')} ~ {prx})")
            parts.append(f"(o.observers_raw IS NULL AND {F('s.authors_raw')} ~ {prx})")
        w.add("(" + " OR ".join(parts) + ")" if parts else "FALSE")
    # место
    if f.place_id is not None:
        rx = R.any_regex(pr.place_by_id or [])
        if rx:
            w.add(f"(o.place_id = {w.p(f.place_id)} OR (o.place_id IS NULL AND {F('o.toponym_raw')} ~ {w.p(rx)}))")
        else:
            w.add(f"o.place_id = {w.p(f.place_id)}")
    if pr.place is not None:
        res = pr.place
        parts = []
        if res.ids:
            parts.append(f"o.place_id = ANY(CAST({w.p(res.ids)} AS bigint[]))")
        rx = res.regex
        if rx:
            parts.append(f"(o.place_id IS NULL AND {F('o.toponym_raw')} ~ {w.p(rx)})")
        w.add("(" + " OR ".join(parts) + ")" if parts else "FALSE")
    # фенофаза
    if f.phenophase_id is not None:
        w.add(f"o.phenophase_id = {w.p(f.phenophase_id)}")
    # показатель
    if f.indicator:
        w.add(f"{F('o.indicator')} LIKE {w.p('%' + like_escape(fold(f.indicator)) + '%')} ESCAPE '\\'")
    # даты: пересечение интервалов
    if f.date_from:
        w.add(f"COALESCE(o.date_to, o.date_from) >= {w.p(f.date_from)}")
    if f.date_to:
        w.add(f"COALESCE(o.date_from, o.date_to) <= {w.p(f.date_to)}")
    if f.years:
        w.add(f"o.obs_year = ANY(CAST({w.p(list(f.years))} AS integer[]))")
    if f.doc_years:
        w.add(f"d.year = ANY(CAST({w.p(list(f.doc_years))} AS integer[]))")
    if f.flagged is True:
        w.add("jsonb_array_length(o.flags) > 0")
    elif f.flagged is False:
        w.add("jsonb_array_length(o.flags) = 0")
    # свободный текст: все слова (по основам) в одном из текстовых полей
    if f.q:
        hay = F("concat_ws(' ', o.object_raw, o.event_raw, o.quote, o.note, o.toponym_raw, o.pub_title, "
                "o.indicator, o.observers_raw)")
        terms = [R.stem(x) for x in words(f.q)] or [fold(f.q)]
        for term in terms[:10]:
            if term:
                w.add(f"{hay} LIKE {w.p('%' + like_escape(term) + '%')} ESCAPE '\\'")
    return w


def order_by(f: SearchFilters) -> str:
    field_ = f.sort or "date_from"
    direction = "DESC" if (f.order or "asc") == "desc" else "ASC"
    expr = SORT_EXPR[field_]  # KeyError невозможен: sort проверен pydantic Literal
    parts = [f"{expr} {direction} NULLS LAST"]
    if field_ == "date_from":
        parts.append(f"o.date_from {direction} NULLS LAST")
    if field_ != "obs_year":
        parts.append(f"o.obs_year {direction} NULLS LAST")
    parts.append("o.id ASC")
    return "ORDER BY " + ", ".join(parts)


def build_page_sql(pr: Prepared, page: int, page_size: int) -> tuple[str, dict[str, Any]]:
    w = build_where(pr)
    sql = (f"SELECT {SELECT_SQL},\n       COUNT(*) OVER () AS _total\n{FROM_SQL}{w.sql()}\n{order_by(pr.filters)}\n"
           f"LIMIT {w.p(page_size)} OFFSET {w.p((page - 1) * page_size)}")
    return sql, w.params


def build_count_sql(pr: Prepared) -> tuple[str, dict[str, Any]]:
    w = build_where(pr)
    return f"SELECT COUNT(*) AS n\n{FROM_SQL}{w.sql()}", w.params


def run_page(conn, pr: Prepared, page: int, page_size: int) -> tuple[list[dict], int]:
    sql, params = build_page_sql(pr, page, page_size)
    rows = [dict(r) for r in conn.execute(text(sql), params).mappings()]
    if rows:
        total = int(rows[0]["_total"])
    elif page > 1:
        csql, cparams = build_count_sql(pr)
        total = int(conn.execute(text(csql), cparams).scalar_one())
    else:
        total = 0
    return rows, total


# --------------------------------------------------------------------------- строка БД → ObservationListItem

def _iso(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "tzinfo") and hasattr(v, "hour"):  # datetime → RFC 3339 UTC

        if v.tzinfo is not None:
            v = v.astimezone(UTC)
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for c in OBS_COLUMNS:
        v = row.get(c)
        if c in ("date_from", "date_to", "created_at", "updated_at"):
            v = _iso(v)
        elif c == "confidence" and v is not None:
            v = round(float(v), 4)
        elif c == "value_num" and v is not None:
            v = float(v)
        elif c == "quote_span" and v is not None:
            v = list(v)
        elif c in ("flags", "bboxes") and v is None:
            v = []
        item[c] = v
    item["person_ids"] = list(row.get("person_ids") or [])
    # порядок ключей как в ObservationCore: person_ids после observers_raw
    ordered = {}
    for k, v in item.items():
        if k == "person_ids":
            continue
        ordered[k] = v
        if k == "observers_raw":
            ordered["person_ids"] = item["person_ids"]
    ordered["labels"] = {
        "taxon_name_ru": row.get("l_taxon_name_ru"),
        "taxon_name_lat": row.get("l_taxon_name_lat"),
        "taxon_group": row.get("l_taxon_group"),
        "place_name": row.get("l_place_name"),
        "phenophase_code": row.get("l_phenophase_code"),
        "phenophase_name": row.get("l_phenophase_name"),
        "persons": list(row.get("l_persons") or []),
        "document_title": row.get("l_document_title"),
        "book_no": row.get("l_book_no"),
        "book_year": row.get("l_book_year"),
    }
    return ordered


_SAFE_NAME = re.compile(r"^[a-z_]+$")
assert all(_SAFE_NAME.match(c) for c in OBS_COLUMNS)
