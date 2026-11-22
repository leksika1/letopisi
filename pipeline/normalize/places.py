"""Сопоставление топонима (как в тексте) с каталогом places/place_names.

Шаги:
1. точное совпадение всей строки с вариантом написания («р. Паз», «о-ве Варлама»);
2. поиск каждого слова строки среди названий каталога с учётом склонений:
   - русские окончания: «Ворьемы/Ворьеме/Ворьему» → «Ворьема», «Калкупи» → «Калкупя»,
     «Никеле» → «Никель», «Заполярном» → «Заполярный» (+ лемма pymorphy3);
   - финские формы на -ярви/-йоки/-коски/-саари/-апа не склоняются — сравниваются как есть;
   - нечётко (rapidfuzz ratio ≥ 90) для слов от 6 букв;
3. родовое слово перед названием («озеро», «оз.», «р.», «гора», «г.», «пос.», «о.») проверяется
   по категории места: «у озера Каскама» не сопоставляется с горой Каскама → place_unmatched;
4. общие выражения «в заповеднике», «на территории заповедника» → место «Пасвик» (Решения, п. 14).

Возвращается первое по тексту найденное место (основной топоним записи).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from .dicts import Dictionaries, load
from .text import fold, lemma

GENERIC = {
    "озеро": "озеро", "озера": "озеро", "озере": "озеро", "озеру": "озеро", "оз": "озеро",
    "река": "река", "реки": "река", "реке": "река", "реку": "река", "р": "река", "ручей": "ручей",
    "ручья": "ручей", "руч": "ручей",
    "гора": "гора", "горы": "гора", "горе": "гора", "гору": "гора", "г": "гора",
    "остров": "остров", "острова": "остров", "острове": "остров", "о": "остров", "о-в": "остров",
    "о-ва": "остров", "о-ве": "остров",
    "поселок": "поселок", "поселка": "поселок", "поселке": "поселок", "пос": "поселок", "п": "поселок",
    "пгт": "поселок", "урочище": "урочище", "урочища": "урочище", "ур": "урочище",
    "болото": "болото", "болота": "болото", "болоте": "болото", "залив": "залив", "залива": "залив",
}
# какие категории каталога допустимы для родового слова
CATEGORY_OK = {
    "озеро": ("озеро",), "река": ("река", "ручей"), "ручей": ("ручей", "река"), "гора": ("гора", "населённый пункт"),
    "остров": ("остров",), "поселок": ("поселок", "населённый пункт"), "урочище": ("урочище",),
    "болото": ("болот",), "залив": ("залив",),
}
_RESERVE_RE = re.compile(r"заповедник|пасвик", re.I)
_FINNISH = ("ярви", "йоки", "коски", "саари", "апа", "фосс", "лампола", "ламполо", "ваара")


@dataclass
class PlaceMatch:
    place_id: int | None = None
    matched: str | None = None
    method: str | None = None
    category_conflict: bool = False


def _variants(word: str) -> list[str]:
    """Кандидаты начальной формы для склоняемого топонима."""
    w = fold(word)
    out = [w]
    if w.endswith(_FINNISH):
        return out
    rules = [("ой", "а"), ("ою", "а"), ("ы", "а"), ("е", "а"), ("у", "а"), ("и", "я"), ("е", "я"), ("ю", "я"),
             ("ей", "я"), ("ом", "ый"), ("ого", "ый"), ("ом", ""), ("а", ""), ("е", "ь"), ("я", "ь"), ("и", "ь"),
             ("ем", "ь"), ("ем", "ий"), ("ого", "ий"), ("ом", "ий")]
    for suf, rep in rules:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            out.append(w[: -len(suf)] + rep)
    lw = lemma(w)
    if lw not in out:
        out.append(lw)
    return out


class PlaceIndex:
    def __init__(self, d: Dictionaries):
        self.d = d
        self.full: dict[str, int] = {}
        self.word: dict[str, int] = {}
        self.cat: dict[int, str] = {p["id"]: fold(p.get("category") or "") for p in d.places}
        self.reserve_id = next((p["id"] for p in d.places if fold(p["name"]) == "пасвик"), None)
        names = [(p["id"], p["name"]) for p in d.places] + [(n["place_id"], n["name"]) for n in d.place_names]
        for pid, name in names:
            f = fold(name)
            self.full.setdefault(f, pid)
            ws = [w for w in re.findall(r"[а-яa-z]+(?:-[а-яa-z]+)*", f) if fold(w) not in GENERIC and len(w) >= 3]
            if len(ws) == 1:
                self.word.setdefault(ws[0], pid)
        self.word_list = list(self.word)

    def match(self, raw: str | None) -> PlaceMatch:
        if not raw or not raw.strip():
            return PlaceMatch()
        f = fold(raw)
        if f in self.full:
            return PlaceMatch(self.full[f], raw, "exact")
        tokens = re.findall(r"[а-яa-z]+(?:-[а-яa-z]+)*\.?", f)
        conflict = False
        prev_generic: str | None = None
        for tok in tokens:
            bare = tok.rstrip(".")
            if bare in GENERIC:
                prev_generic = GENERIC[bare]
                continue
            if len(bare) < 3:
                prev_generic = None
                continue
            pid, how = self._word(bare)
            if pid is not None:
                if prev_generic and not self._category_ok(pid, prev_generic):
                    conflict = True
                    prev_generic = None
                    continue
                return PlaceMatch(pid, tok, how)
            prev_generic = None
        if not conflict and self.reserve_id and _RESERVE_RE.search(f):
            return PlaceMatch(self.reserve_id, "заповедник", "reserve")
        return PlaceMatch(category_conflict=conflict)

    def _word(self, w: str) -> tuple[int | None, str | None]:
        for v in _variants(w):
            if v in self.word:
                return self.word[v], "word"
        if len(w) >= 6 and w[0].isalpha():
            r = process.extractOne(w, self.word_list, scorer=fuzz.ratio, score_cutoff=90)
            if r:
                return self.word[r[0]], "fuzzy"
        return None, None

    def _category_ok(self, pid: int, generic: str) -> bool:
        cat = self.cat.get(pid, "")
        if not cat:
            return True
        return any(fold(c) in cat for c in CATEGORY_OK.get(generic, (generic,)))


@lru_cache(maxsize=1)
def default_index() -> PlaceIndex:
    return PlaceIndex(load())


def match_place(raw: str | None) -> PlaceMatch:
    return default_index().match(raw)


def find_places_in_text(text: str) -> list[tuple[int, str]]:
    """Все места каталога, упомянутые в тексте (для заголовков таблиц и подписей)."""
    idx = default_index()
    out, seen = [], set()
    for m in re.finditer(r"(?:(?:р|оз|г|о|пос|ур)\.\s*|(?:реки|река|озера|озеро|горы|гора|острова|окрестностях)\s+)?"
                         r"[А-ЯЁ][а-яё]+(?:-[А-Яа-яЁё]+)?", text or ""):
        pm = idx.match(m.group(0))
        if pm.place_id and pm.method != "reserve" and pm.place_id not in seen:
            seen.add(pm.place_id)
            out.append((pm.place_id, m.group(0)))
    return out
