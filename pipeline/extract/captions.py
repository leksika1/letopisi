"""Подписи к фотографиям: «Рисунок 8.483 - Обыкновенная бурозубка (Sorex araneus). О. Варлама,
29.07.2020 г. Фото: Валова Е.В.» → запись «вид · место · дата · наблюдатель (автор фото)».

Подпись разбирается детерминированно, только если в ней есть дата и объект — один вид
(найден в справочнике или указан латинский бином). Иначе подпись уходит в LLM (если есть дата)
или пропускается (пейзажи без даты).
"""
from __future__ import annotations

import re

from pipeline.normalize.taxa import _LAT_BINOM_RE, match_taxon

from .tables.common import Draft, span_quote

CAPTION_RE = re.compile(r"^\s*Рис(?:унок|унки|\.)\s*[\d.]+(?:\s*[-–—]\s*[\d.]+)?\s*[-–—]\s*", re.I)
_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?:\s*г\.?)?")
_PHOTO = re.compile(r"Фото\s*:\s*", re.I)
_SUBJ_END = re.compile(r"\.\s+(?=[А-ЯЁA-Z«])")


def is_caption(text: str) -> bool:
    return bool(CAPTION_RE.match(text))


def has_date(text: str) -> bool:
    return bool(_DATE.search(text))


def parse_caption(text: str, offset: int = 0) -> tuple[list[Draft], bool]:
    """→ (черновики, разобрано_детерминированно). offset — начало подписи в chunk.text."""
    m = CAPTION_RE.match(text)
    if not m:
        return [], False
    body_start = m.end()
    body = text[body_start:]
    dm = None
    pm = _PHOTO.search(body)
    for d in _DATE.finditer(body):
        if pm is None or d.start() < pm.start():
            dm = d
    if dm is None:
        return [], True  # без даты — не наблюдение (пейзаж, схема)
    se = _SUBJ_END.search(body, 0, dm.start())
    subj_end = se.start() if se else None
    if subj_end is None:
        return [], False
    subject = re.sub(r"\s+", " ", body[:subj_end]).strip()
    place = re.sub(r"\s+", " ", body[se.end(): dm.start()]).strip(" ,.;")
    if len(subject) > 80 or re.search(r"\sи\s", subject) or not subject:
        return [], False
    tm = match_taxon(subject)
    if tm.taxon_id is None and not tm.ambiguous and not _LAT_BINOM_RE.search(subject):
        return [], False
    observers = None
    if pm:
        observers = re.sub(r"\s+", " ", body[pm.end():]).strip() or None
    q_end = len(text)
    q, span = span_quote(text, 0, q_end)
    f = {
        "record_type": "observation", "object_type": "taxon", "object_raw": subject,
        "date_raw": re.sub(r"\s+", " ", dm.group(0)).strip(), "toponym_raw": place or None,
        "event_raw": "встреча (фото)", "observers_raw": observers,
    }
    d = Draft(f, q, (span[0] + offset, span[1] + offset), "caption", False,
              ["Подпись к фотографии; наблюдатель — автор фото."] if observers else ["Подпись к фотографии."],
              observers_from_section=False)
    return [d], True
