"""Сборка записи Observation по контракту из черновика (парсер таблицы / подпись / LLMRecord) и контекста.

Здесь же — все шаги после извлечения: привязка цитаты (anchor), нормализация (даты, таксон,
место, люди, фенофаза), флаги и confidence. Правила:
- ``observers_raw`` — из текста; если в тексте нет, а у раздела (или его предка) есть авторы —
  авторы раздела с note «наблюдатель по автору раздела» (не для публикаций, календаря природы и
  подписей к фото);
- ``obs_year`` — год из даты; иначе год, указанный парсером (столбец таблицы) или моделью;
  иначе год книги (с объяснением в note);
- ``year_outside_book`` ⇔ obs_year ≠ год книги;
- для публикаций ``obs_year = pub_year``, ``person_ids`` — авторы-сотрудники.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.anchor import anchor
from pipeline.flags import compute_flags, confidence
from pipeline.normalize.dates import parse_date
from pipeline.normalize.persons import match_people
from pipeline.normalize.phenophases import code_to_id, match_phenophase
from pipeline.normalize.places import match_place
from pipeline.normalize.taxa import match_taxon, taxon_group

from .prompt import PROMPT_VERSION, LLMRecord
from .tables.common import Draft

EXTRACTOR_VERSION = f"core-0.1+{PROMPT_VERSION}"
OBS_FIELDS = [
    "record_type", "object_type", "object_raw", "taxon_id", "date_raw", "date_from", "date_to", "date_precision",
    "obs_year", "toponym_raw", "place_id", "coords_raw", "habitat_raw", "event_raw", "phenophase_id",
    "phenophase_raw", "indicator", "value_num", "value_raw", "unit", "observers_raw", "pub_authors", "pub_year",
    "pub_title", "pub_venue",
]


@dataclass
class DocContext:
    document_id: int
    year: int | None
    title: str
    sections: object  # SectionsResult
    lines: dict  # chunk_id → {"bbox", "lines"}


def draft_from_llm(r: LLMRecord) -> Draft:
    f = r.model_dump()
    note = f.pop("note", None)
    quote = f.pop("quote")
    ref = f.pop("chunk_ref", 1)
    return Draft(f, quote, None, "llm", False, [note] if note else [], chunk_ref=ref)


_NUM_THEN_REST = re.compile(r"^\s*([-+−]?\d+(?:[.,]\d+)?(?:\s*[-–/]\s*\d+(?:[.,]\d+)?)?)\s*(\S.*)?$")


def split_value(value_raw, value_num, unit):
    """«10,1°С» + unit «°С» → ("10,1", 10.1, "°С"): единица не дублируется в value_raw.

    Модель иногда кладёт единицу и в value_raw, и в unit («25 ловушко-суток» + «ловушко-сутки»).
    Если value_raw = число + хвост и единица известна — хвост отрезаем; если единицы нет — хвост и есть единица.
    """
    if not isinstance(value_raw, str) or not value_raw.strip():
        return value_raw, value_num, unit
    m = _NUM_THEN_REST.match(value_raw)
    if not m:
        return value_raw.strip(), value_num, unit
    num, rest = m.group(1).strip(), (m.group(2) or "").strip()
    if value_num is None and not re.search(r"[-–/]", num[1:]):
        try:
            value_num = float(num.replace(",", ".").replace("−", "-"))
        except ValueError:
            pass
    if rest:
        if not unit:
            unit = rest
        return num, value_num, unit
    return num, value_num, unit


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def build(d: Draft, chunk: dict, dc: DocContext, obs_id: int) -> dict:
    f = {k: _clean(v) for k, v in d.fields.items()}
    year_hint = f.pop("_year_hint", None)
    ph_code = f.pop("_phenophase_code", None)
    notes = list(d.notes)
    sid = dc.sections.chunk_section.get(chunk["id"], chunk.get("section_id")) if dc.sections else chunk.get("section_id")
    obs = {k: f.get(k) for k in OBS_FIELDS}
    obs["person_ids"] = []
    obs["value_raw"], obs["value_num"], obs["unit"] = split_value(obs["value_raw"], obs["value_num"], obs["unit"])

    # --- привязка цитаты
    meta = dc.lines.get(chunk["id"]) or {}
    a = anchor(d.quote, chunk["text"], meta.get("lines"), meta.get("bbox"), d.quote_span)
    obs["quote"] = a.quote if a.found else d.quote
    obs["quote_span"] = list(a.span) if a.found else None
    obs["bboxes"] = a.bboxes if a.found else []
    if a.method == "fuzzy":
        notes.append(f"цитата привязана нечётко (сходство {a.score:.0f}), заменена дословным текстом источника")

    # --- даты и год
    is_pub = obs["record_type"] == "publication"
    if is_pub and obs.get("pub_year") and not obs.get("date_raw"):
        obs["obs_year"] = obs["pub_year"]
    hint = obs.get("obs_year") or year_hint or dc.year
    dr = parse_date(obs.get("date_raw"), hint)
    date_amb = dr.ambiguous
    if dr.ok:
        obs["date_from"], obs["date_to"], obs["date_precision"] = dr.date_from, dr.date_to, dr.precision
        obs["obs_year"] = dr.year if dr.year_from_text or not obs.get("obs_year") else obs["obs_year"]
        if dr.note:
            notes.append(dr.note)
    else:
        obs["date_from"] = obs["date_to"] = obs["date_precision"] = None
        if obs.get("date_raw") and dr.note:
            notes.append(dr.note)
        if obs.get("obs_year") is None:
            obs["obs_year"] = year_hint or dc.year
            if not obs.get("date_raw"):
                notes.append("Даты в цитате нет; год наблюдения — по году книги." if not year_hint
                             else "Даты в цитате нет; год — из заголовка таблицы.")
    if is_pub:
        obs["obs_year"] = obs.get("pub_year") or obs["obs_year"]

    # --- таксон
    tm = match_taxon(obs["object_raw"]) if obs["object_type"] in ("taxon", "publication") else None
    taxon_unm = taxon_amb = False
    if tm is not None:
        obs["taxon_id"] = tm.taxon_id
        taxon_amb = tm.ambiguous
        taxon_unm = tm.unmatched
        if obs["object_type"] == "taxon" and taxon_unm and d.strategy == "table:calendar":
            obs["object_type"] = "phenomenon"  # «Появление комаров» и т.п. — явление без вида в справочнике
            taxon_unm = False
        if taxon_amb and tm.candidates:
            notes.append("кандидаты справочника: " + ", ".join(str(x) for x in tm.candidates)
                         + (" (совпадение только по роду/основному слову)" if tm.taxon_id else ""))
    else:
        obs["taxon_id"] = None

    # --- место
    place_unm = False
    if obs.get("toponym_raw"):
        pm = match_place(obs["toponym_raw"])
        obs["place_id"] = pm.place_id
        place_unm = pm.place_id is None
        if pm.category_conflict:
            notes.append("родовое слово топонима не совпадает с категорией места в каталоге")
    else:
        obs["place_id"] = None

    # --- люди
    person_unm = False
    from_section = False
    if is_pub:
        ids, _ = match_people(obs.get("pub_authors"))
        obs["person_ids"] = ids
    else:
        if not obs.get("observers_raw") and d.observers_from_section and dc.sections:
            auth = dc.sections.authors_for(sid)
            if auth:
                obs["observers_raw"] = auth["authors_raw"]
                from_section = True
                notes.append("наблюдатель по автору раздела")
        if obs.get("observers_raw"):
            ids, refs = match_people(obs["observers_raw"])
            obs["person_ids"] = ids
            person_unm = any(r.person_id is None for r in refs) or not refs

    # --- фенофаза
    grp = taxon_group(obs.get("taxon_id"))
    if ph_code:
        obs["phenophase_id"] = code_to_id(ph_code)
    elif obs["record_type"] == "observation":
        pid, _ = match_phenophase(obs.get("phenophase_raw"), obs.get("event_raw"), group=grp)
        if pid is None and d.strategy in ("table:calendar", "llm"):
            pid, _ = match_phenophase(obs["object_raw"], group=grp)
        obs["phenophase_id"] = pid
    else:
        obs["phenophase_id"] = None

    # --- источник
    loc = dict(chunk.get("locator") or {})
    obs.update({
        "id": obs_id, "document_id": dc.document_id, "section_id": sid, "chunk_id": chunk["id"], "locator": loc,
        "printed_pages": d.printed_pages or loc.get("printed_page") or (str(loc["pdf_page"]) if loc.get("pdf_page") else None),
        "status": "auto", "extractor_version": EXTRACTOR_VERSION,
    })
    flags = compute_flags(
        obs, doc_year=dc.year, quote_found=a.found, date_ambiguous=date_amb, taxon_unmatched=taxon_unm,
        taxon_ambiguous=taxon_amb, place_unmatched=place_unm, person_unmatched=person_unm,
        table_uncertain=d.uncertain, ocr_confidence=chunk.get("ocr_confidence"),
    )
    obs["flags"] = flags
    obs["confidence"] = confidence(d.strategy, flags, fuzzy_anchor=a.method == "fuzzy", observers_from_section=from_section)
    obs["note"] = " ".join(n for n in notes if n) or None
    return obs
