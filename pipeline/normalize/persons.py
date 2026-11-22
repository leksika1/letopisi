"""Разбор строки наблюдателей/авторов и сопоставление с каталогом persons.

Поддерживаемые формы:
«Хохлов, Сметанников, Шершнев» · «Крюкова С.А.» · «Поликарпова Н.В., Бычков Ю.М» ·
«А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев» · «Валовой Е.В.» (дательный/родительный падеж) ·
«Дмитренко, 2020» (ссылка «фамилия, год»).

Сопоставление по фамилии: сначала точное (ё/е), затем снятие падежных окончаний
(«Валовой»/«Крюковой» → «Валова»/«Крюкова», «Хохлова»/«Хохлову» → «Хохлов»). «Сметанников» и
«Сметанникова» — разные записи каталога: точное совпадение всегда приоритетнее, поэтому
«Сметанникова» не превращается в «Сметанников».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .dicts import Dictionaries, load
from .text import fold

_UP = "А-ЯЁ"
_LO = "а-яё"
SURNAME = rf"[{_UP}][{_LO}]+(?:-[{_UP}][{_LO}]+)?"
INITS = rf"[{_UP}]\.\s?(?:[{_UP}]\.?)?"
_PERSON_RE = re.compile(rf"(?:(?P<i1>{INITS})\s*(?P<s1>{SURNAME})|(?P<s2>{SURNAME})(?:\s+(?P<i2>{INITS}))?)")
AUTHOR_LINE_RE = re.compile(
    rf"^\s*(?:(?:{SURNAME}\s+{INITS}|{INITS}\s*{SURNAME})\s*(?:,|\s+и\s+|$)\s*)+\s*$"
)
_STOP = {"Фото", "Рисунок", "Таблица", "Рис", "Под", "Ред", "См", "Табл", "Примечание", "Всего", "Итого"}


@dataclass
class PersonRef:
    surname: str
    initials: str | None
    raw: str
    person_id: int | None = None


def is_author_line(line: str) -> bool:
    """Строка — список авторов вида «Фамилия И.О., Фамилия И.О.» (шапка раздела)."""
    s = line.strip()
    return bool(s) and len(s) < 250 and bool(AUTHOR_LINE_RE.match(s))


def parse_people(raw: str | None) -> list[PersonRef]:
    """Список людей из строки как в тексте."""
    if not raw:
        return []
    s = re.sub(r"\(\s*|\s*\)", " ", raw)
    s = re.sub(r",?\s*(?:19|20)\d{2}\s*(?:г\.?)?", " ", s)  # «Дмитренко, 2020»
    parts = re.split(r",|;|\s+и\s+|\s+&\s+", s)
    out: list[PersonRef] = []
    for part in parts:
        part = part.strip(" .")
        if not part:
            continue
        m = _PERSON_RE.search(part + ("." if re.search(rf"[{_UP}]$", part) else ""))
        if not m:
            continue
        sur = m.group("s1") or m.group("s2")
        ini = m.group("i1") or m.group("i2")
        if sur in _STOP or len(sur) < 3:
            continue
        ini = re.sub(r"\s+", "", ini) if ini else None
        out.append(PersonRef(sur, ini, part))
    return out


def _variants(surname: str) -> list[str]:
    w = fold(surname)
    out = [w]
    rules = [("ой", "а"), ("ою", "а"), ("ую", "а"), ("ым", ""), ("ом", ""), ("у", ""), ("а", ""), ("е", ""),
             ("ой", "ая"), ("ому", "ий"), ("ого", "ий"), ("ым", "ий"), ("ему", "ий"), ("ина", "ин"),
             ("ину", "ин"), ("ой", "ий")]
    for suf, rep in rules:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            out.append(w[: -len(suf)] + rep)
    return out


class PersonIndex:
    def __init__(self, d: Dictionaries):
        self.by_surname: dict[str, list[dict]] = {}
        for p in d.persons:
            self.by_surname.setdefault(fold(p["surname"]), []).append(p)

    def find(self, ref: PersonRef) -> int | None:
        for i, v in enumerate(_variants(ref.surname)):
            cands = self.by_surname.get(v)
            if not cands:
                continue
            if len(cands) > 1 and ref.initials:
                exact = [c for c in cands if c.get("initials") and fold(c["initials"]) == fold(ref.initials)]
                if len(exact) == 1:
                    return exact[0]["id"]
            if len(cands) == 1:
                c = cands[0]
                if ref.initials and c.get("initials") and fold(c["initials"])[:2] != fold(ref.initials)[:2]:
                    continue  # однофамилец с другими инициалами
                return c["id"]
        return None

    def match(self, raw: str | None) -> tuple[list[int], list[PersonRef]]:
        """→ (person_ids без повторов, все разобранные ссылки с person_id)."""
        refs = parse_people(raw)
        ids: list[int] = []
        for r in refs:
            r.person_id = self.find(r)
            if r.person_id and r.person_id not in ids:
                ids.append(r.person_id)
        return ids, refs


@lru_cache(maxsize=1)
def default_index() -> PersonIndex:
    return PersonIndex(load())


def match_people(raw: str | None) -> tuple[list[int], list[PersonRef]]:
    return default_index().match(raw)
