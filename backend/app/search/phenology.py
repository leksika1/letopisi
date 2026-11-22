"""GET /api/phenology: даты фенофаз вида по годам (например табл. 7.10: фенофаза × 2018/2019/2020).

Строка — одна запись наблюдения с заданной фенофазой; несколько записей на одну пару
(фенофаза, год) — например из табл. 7.10 и 7.11 — возвращаются все, клиент сводит их сам.
Сортировка: порядок фенофазы в шкале (``phenophases.ord``), год, дата, id.
Записи ``rejected`` не возвращаются; записи без ``obs_year`` пропускаются (``year`` обязателен в PhenologyRow).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .sql import _iso


class NotFound(LookupError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def get_phenology(conn, taxon_id: int, phenophase_id: int | None = None,
                  years: list[int] | None = None) -> list[dict[str, Any]]:
    if conn.execute(text("SELECT 1 FROM taxa WHERE id = :id"), {"id": taxon_id}).first() is None:
        raise NotFound(f"Вид с id={taxon_id} не найден")
    params: dict[str, Any] = {"tid": taxon_id}
    where = ["o.taxon_id = :tid", "o.phenophase_id IS NOT NULL", "o.obs_year IS NOT NULL",
             "o.status <> 'rejected'"]
    if phenophase_id is not None:
        where.append("o.phenophase_id = :ph")
        params["ph"] = phenophase_id
    if years:
        where.append("o.obs_year = ANY(CAST(:years AS integer[]))")
        params["years"] = list(years)
    sql = f"""
        SELECT o.taxon_id, o.phenophase_id, ph.code AS phenophase_code, ph.name_ru AS phenophase_name,
               o.obs_year AS year, o.date_from, o.date_to, o.id AS observation_id
        FROM observations o JOIN phenophases ph ON ph.id = o.phenophase_id
        WHERE {' AND '.join(where)}
        ORDER BY ph.ord, ph.id, o.obs_year, o.date_from NULLS LAST, o.id"""
    out = []
    for r in conn.execute(text(sql), params).mappings():
        out.append({
            "taxon_id": r["taxon_id"], "phenophase_id": r["phenophase_id"],
            "phenophase_code": r["phenophase_code"], "phenophase_name": r["phenophase_name"],
            "year": r["year"], "date_from": _iso(r["date_from"]), "date_to": _iso(r["date_to"]),
            "observation_id": r["observation_id"],
        })
    return out
