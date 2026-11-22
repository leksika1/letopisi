"""Автоматические флаги и итоговая достоверность (confidence) записи.

Флаги (enum Flag контракта):
- ``quote_not_found`` — цитата не найдена в тексте фрагмента ни точно, ни нечётко;
- ``value_not_in_quote`` — число из value_raw/value_num (или число словом) не встречается в цитате;
- ``date_not_in_quote`` — date_raw не встречается в цитате;
- ``date_ambiguous`` — дату не удалось однозначно перевести в интервал (нет года, «зимой» без года,
  несколько дат, лишний текст);
- ``year_outside_book`` — obs_year ≠ году книги (информативный; ставится тогда и только тогда);
- ``taxon_unmatched`` / ``taxon_ambiguous`` — объект-таксон не найден в справочнике / найдено
  несколько кандидатов или только род/«голова» названия;
- ``place_unmatched`` — топоним есть, но в каталоге не найден (или родовое слово противоречит
  категории места: «озеро Каскама» при горе Каскама);
- ``person_unmatched`` — хотя бы один наблюдатель не найден в каталоге сотрудников
  (для публикаций не ставится: соавторы-не сотрудники — норма);
- ``low_ocr`` — средняя уверенность OCR фрагмента < 0.8;
- ``table_parse_uncertain`` — парсер таблицы не уверен в соответствии ячеек и столбцов.

Достоверность: ``confidence = база(стратегия) − Σ штрафов``, ограничено [0.05, 0.99].
База: парсер таблицы 0.95, подпись к фото 0.92, LLM 0.85. Штрафы — ``PENALTY`` ниже;
дополнительно −0.05 за нечёткую привязку цитаты и −0.05, если наблюдатель взят из авторов раздела.
"""
from __future__ import annotations

import re

from pipeline.normalize.dates import date_in_text
from pipeline.normalize.text import NUMBER_WORDS, fold, numbers

BASE = {"table": 0.95, "caption": 0.92, "llm": 0.85}
PENALTY = {
    "quote_not_found": 0.35,
    "value_not_in_quote": 0.25,
    "date_not_in_quote": 0.20,
    "date_ambiguous": 0.10,
    "taxon_unmatched": 0.10,
    "taxon_ambiguous": 0.10,
    "place_unmatched": 0.05,
    "person_unmatched": 0.05,
    "year_outside_book": 0.0,
    "low_ocr": 0.20,
    "table_parse_uncertain": 0.20,
}
FLAG_ORDER = list(PENALTY)


def value_in_quote(value_raw: str | None, value_num: float | None, quote: str | None) -> bool:
    if value_raw is None and value_num is None:
        return True
    if not quote:
        return False
    qn = set(numbers(quote))
    vals = numbers(value_raw) if value_raw else []
    if not vals and value_num is not None and not value_raw:
        v = f"{value_num:g}"
        vals = [v]
    if vals:
        return all(v in qn for v in vals[:2])
    # число словом («Четыре», «две берлоги»)
    fq = fold(quote)
    ws = [w for w in re.findall(r"[а-я]+", fold(value_raw)) if w in NUMBER_WORDS]
    if ws:
        return all(w in fq for w in ws)
    return fold(value_raw) in fq


def compute_flags(obs: dict, *, doc_year: int | None, quote_found: bool, date_ambiguous: bool,
                  taxon_unmatched: bool, taxon_ambiguous: bool, place_unmatched: bool, person_unmatched: bool,
                  table_uncertain: bool, ocr_confidence: float | None) -> list[str]:
    flags: set[str] = set()
    if not quote_found:
        flags.add("quote_not_found")
    else:
        if not value_in_quote(obs.get("value_raw"), obs.get("value_num"), obs.get("quote")):
            flags.add("value_not_in_quote")
        if obs.get("date_raw") and not date_in_text(obs["date_raw"], obs.get("quote")):
            flags.add("date_not_in_quote")
    if date_ambiguous:
        flags.add("date_ambiguous")
    if obs.get("obs_year") is not None and doc_year is not None and obs["obs_year"] != doc_year:
        flags.add("year_outside_book")
    if obs.get("object_type") == "taxon":
        if taxon_ambiguous:
            flags.add("taxon_ambiguous")
        elif taxon_unmatched:
            flags.add("taxon_unmatched")
    if place_unmatched:
        flags.add("place_unmatched")
    if person_unmatched:
        flags.add("person_unmatched")
    if table_uncertain:
        flags.add("table_parse_uncertain")
    if ocr_confidence is not None and ocr_confidence < 0.8:
        flags.add("low_ocr")
    return [f for f in FLAG_ORDER if f in flags]


def confidence(strategy: str, flags: list[str], *, fuzzy_anchor: bool = False, observers_from_section: bool = False) -> float:
    base = BASE.get(strategy.split(":")[0], 0.85)
    c = base - sum(PENALTY.get(f, 0) for f in flags)
    if fuzzy_anchor:
        c -= 0.05
    if observers_from_section:
        c -= 0.05
    return round(min(0.99, max(0.05, c)), 2)
