"""GET /api/places/{id}/species: виды, отмеченные в месте.

Запись относится к месту, если ``place_id`` = id места ИЛИ (``place_id`` не задан и
``toponym_raw`` содержит название места / вариант написания в любом падеже — см.
``resolve.place_word_regex``). Учитываются только записи с ``taxon_id`` (вид известен) и не ``rejected``.
Фильтры: ``year`` (по ``obs_year``), ``group`` (группа таксона). Сортировка: число записей ↓, название.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from . import resolve as R
from .phenology import NotFound
from .sql import _iso


def place_species(conn, place_id: int, year: int | None = None, group: str | None = None) -> dict[str, Any]:
    place, patterns = R.place_by_id(conn, place_id)
    if place is None:
        raise NotFound(f"Место с id={place_id} не найдено")
    params: dict[str, Any] = {"pid": place_id}
    match = "o.place_id = :pid"
    rx = R.any_regex(patterns)
    if rx:
        match = f"(o.place_id = :pid OR (o.place_id IS NULL AND {R.sql_fold('o.toponym_raw')} ~ :rx))"
        params["rx"] = rx
    where = [match, "o.taxon_id IS NOT NULL", "o.status <> 'rejected'"]
    if year is not None:
        where.append("o.obs_year = :year")
        params["year"] = year
    if group:
        where.append("t.group_ru = :grp")
        params["grp"] = group
    sql = f"""
        SELECT t.id AS taxon_id, t.name_ru, t.name_lat, t.group_ru, COUNT(*) AS n_records,
               MIN(o.date_from) AS first_date, MAX(COALESCE(o.date_to, o.date_from)) AS last_date
        FROM observations o JOIN taxa t ON t.id = o.taxon_id
        WHERE {' AND '.join(where)}
        GROUP BY t.id, t.name_ru, t.name_lat, t.group_ru
        ORDER BY COUNT(*) DESC, lower(COALESCE(t.name_ru, t.name_lat)), t.id"""
    items = []
    for r in conn.execute(text(sql), params).mappings():
        items.append({
            "taxon_id": r["taxon_id"], "name_ru": r["name_ru"], "name_lat": r["name_lat"],
            "group_ru": r["group_ru"], "n_records": int(r["n_records"]),
            "first_date": _iso(r["first_date"]), "last_date": _iso(r["last_date"]),
        })
    return {
        "place": {
            "id": place["id"], "name": place["name"], "category": place["category"],
            "lat": place["lat"], "lon": place["lon"], "coords_source": place["coords_source"],
        },
        "items": items,
    }
