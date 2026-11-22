"""GET /api/suggest/{taxa|places|persons|phenophases}?q= → [{id, label, sublabel}].

Ранжирование: совпадение с начала названия (или с начала слова) выше, затем pg_trgm similarity.
Сравнение — без регистра, ё = е. Запрос в косвенном падеже тоже понимается: к строке
добавляется её лемма («лося» → «лось»).
Подписи (openapi.yaml): taxa — name_ru / name_lat; places — name / category;
persons — фамилия + инициалы / годы работы; phenophases — «<code> — <name_ru>» / applies_to.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from pipeline.normalize import phenophases as _nph
from pipeline.normalize.dicts import load as load_fixture_dicts
from pipeline.normalize.text import fold, words

from .resolve import main_lemma
from .sql import like_escape

KINDS = ("taxa", "places", "persons", "phenophases")
MIN_SIM = 0.25


def _q_variants(q: str) -> tuple[str, str]:
    f = fold(q)
    ws = words(f)
    lem = " ".join(main_lemma(w) for w in ws) if ws else f
    return f, lem


def _score_sql(col: str) -> str:
    c = f"translate(lower({col}), 'ё', 'е')"
    return (f"(CASE WHEN {c} LIKE :pref ESCAPE '\\' OR {c} LIKE :pref_l ESCAPE '\\' THEN 2 "
            f"WHEN {c} LIKE :wpref ESCAPE '\\' OR {c} LIKE :wpref_l ESCAPE '\\' THEN 1.5 ELSE 0 END"
            f" + CASE WHEN {c} = :f THEN 1 ELSE 0 END"
            f" + GREATEST(similarity({c}, :f), 0.9 * similarity({c}, :lem)))")


def _params(q: str, limit: int) -> dict[str, Any]:
    f, lem = _q_variants(q)
    return {"f": f, "lem": lem, "pref": like_escape(f) + "%", "pref_l": like_escape(lem) + "%",
            "wpref": "% " + like_escape(f) + "%", "wpref_l": "% " + like_escape(lem) + "%",
            "min": MIN_SIM, "limit": limit}


def suggest(conn, kind: str, q: str, limit: int = 10) -> list[dict[str, Any]]:
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид подсказок: {kind}")
    q = (q or "").strip()
    if not q:
        raise ValueError("пустой запрос")
    limit = max(1, min(int(limit), 50))
    p = _params(q, limit)
    if kind == "taxa":
        sql = f"""
            WITH n AS (
                SELECT taxon_id, name FROM taxon_names
                UNION ALL SELECT id, name_lat FROM taxa
                UNION ALL SELECT id, name_ru FROM taxa WHERE name_ru IS NOT NULL),
            sc AS (SELECT taxon_id, MAX({_score_sql('name')}) AS score FROM n GROUP BY taxon_id)
            SELECT t.id, COALESCE(t.name_ru, t.name_lat) AS label, t.name_lat AS sublabel, sc.score
            FROM sc JOIN taxa t ON t.id = sc.taxon_id
            WHERE sc.score >= :min
            ORDER BY sc.score DESC, lower(COALESCE(t.name_ru, t.name_lat)), t.id LIMIT :limit"""
    elif kind == "places":
        sql = f"""
            WITH n AS (SELECT place_id, name FROM place_names UNION ALL SELECT id, name FROM places),
            sc AS (SELECT place_id, MAX({_score_sql('name')}) AS score FROM n GROUP BY place_id)
            SELECT pl.id, pl.name AS label, pl.category AS sublabel, sc.score
            FROM sc JOIN places pl ON pl.id = sc.place_id
            WHERE sc.score >= :min
            ORDER BY sc.score DESC, lower(pl.name), pl.id LIMIT :limit"""
    elif kind == "persons":
        sql = f"""
            SELECT id, surname || COALESCE(' ' || initials, '') AS label,
                   years_active, year_start, year_end, still_working, {_score_sql('surname')} AS score
            FROM persons
            WHERE {_score_sql('surname')} >= :min
            ORDER BY score DESC, lower(surname), id LIMIT :limit"""
    else:
        # фенофазы: по коду, названию и ключевым словам («зацветание» → 5 «начало цветения»)
        p["rule_id"] = _rule_phenophase_id(conn, q)
        sql = f"""
            SELECT id, code, name_ru, applies_to,
                   (CASE WHEN id = :rule_id THEN 3 ELSE 0 END
                    + CASE WHEN lower(code) = :f THEN 3 ELSE 0 END
                    + {_score_sql("COALESCE(name_ru, '')")}) AS score
            FROM phenophases
            WHERE (CASE WHEN id = :rule_id THEN 3 ELSE 0 END + CASE WHEN lower(code) = :f THEN 3 ELSE 0 END
                   + {_score_sql("COALESCE(name_ru, '')")}) >= :min
            ORDER BY score DESC, ord, id LIMIT :limit"""
    rows = [dict(r) for r in conn.execute(text(sql), p).mappings()]
    out = []
    for r in rows:
        if kind == "persons":
            sub = _years_label(r)
            out.append({"id": r["id"], "label": r["label"], "sublabel": sub})
        elif kind == "phenophases":
            label = f"{r['code']} — {r['name_ru']}" if r["name_ru"] else r["code"]
            out.append({"id": r["id"], "label": label, "sublabel": r["applies_to"]})
        else:
            out.append({"id": r["id"], "label": r["label"], "sublabel": r["sublabel"]})
    return out


def _years_label(r: dict) -> str | None:
    ya = sorted(r.get("years_active") or [])
    parts = []
    if ya:
        parts.append("годы в каталоге: " + ", ".join(str(y) for y in ya))
    if r.get("year_start") or r.get("year_end") or r.get("still_working"):
        end = "наст. время" if r.get("still_working") else (str(r["year_end"]) if r.get("year_end") else "?")
        parts.append(f"работа: {r.get('year_start') or '?'}–{end}")
    return "; ".join(parts) or None


def _rule_phenophase_id(conn, q: str) -> int:
    pid, _ = _nph.match_phenophase(q)
    fx = load_fixture_dicts().phenophase(pid) if pid else None
    if not fx:
        return 0
    r = conn.execute(text("SELECT id FROM phenophases WHERE applies_to = :a AND code = :c"),
                     {"a": fx["applies_to"], "c": fx["code"]}).first()
    return int(r[0]) if r else 0
