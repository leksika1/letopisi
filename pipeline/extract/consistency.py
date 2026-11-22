"""Межполевые правила контракта, которые JSON-схема не выражает, а schema.sql проверяет.

Применяются к каждой записи перед сохранением, чтобы загрузка в БД не падала.
"""

from __future__ import annotations

PUB_FIELDS = ("pub_authors", "pub_year", "pub_title", "pub_venue")


def enforce(obs: dict) -> dict:
    """Приводит запись к правилам schema.sql; возвращает её же (меняет на месте).

    - Поля публикации допустимы только у record_type = publication: у наблюдения ссылка на
      литературу («Крутов и др., 2012») переносится в note, поля обнуляются.
    - Публикация всегда имеет object_type = publication.
    - date_from <= date_to, иначе даты сбрасываются с флагом date_ambiguous.
    """
    if obs.get("record_type") == "publication":
        obs["object_type"] = "publication"
    elif any(obs.get(k) for k in PUB_FIELDS):
        ref = ", ".join(str(obs[k]) for k in ("pub_authors", "pub_year") if obs.get(k))
        if ref:
            note = obs.get("note") or ""
            add = f"Ссылка на литературу: {ref}."
            obs["note"] = f"{note} {add}".strip() if add not in note else note
        for k in PUB_FIELDS:
            obs[k] = None
    df, dt = obs.get("date_from"), obs.get("date_to")
    if df and dt and df > dt:
        obs["date_from"] = obs["date_to"] = obs["date_precision"] = None
        flags = obs.setdefault("flags", [])
        if "date_ambiguous" not in flags:
            flags.append("date_ambiguous")
    return obs
