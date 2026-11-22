"""Сопоставление названия объекта (как в тексте) со справочником taxon_names.

Порядок (первый сработавший уровень выигрывает):
1. точное совпадение нормализованной строки (регистр, ё/е, авторы «L.», «Ehrh.» отброшены);
2. латынь: бином «Geranium sylvaticum» → точное совпадение; затем род, если в справочнике
   ровно один вид этого рода (→ флаг taxon_ambiguous);
3. по леммам (pymorphy3, все слова): лемматизированное название справочника входит
   в лемматизированный текст подряд («Зацветание герани лесной» → «герань лесная»);
   самое длинное совпадение выигрывает;
4. по первому слову русского названия («лебедь» → «Лебедь-кликун», «медведя» → «Бурый медведь»
   по варианту) — только если кандидат единственный, с флагом taxon_ambiguous;
5. нечётко: rapidfuzz ratio ≥ 90 по каждой русской/латинской части.

Несколько разных кандидатов на лучшем уровне → taxon_id = None и taxon_ambiguous.
Ничего не найдено → taxon_unmatched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz, process

from .dicts import Dictionaries, load
from .text import fold, lemma, lemmas

_AUTHOR_RE = re.compile(
    r"\b(L|Ehrh|Mill|Hager|Bab|Desv|Wigg|Web|Pojark|Turcz|Rupr|Herrm|Maxim|Scop|Wither|Soo|Linnaeus|"
    r"Pall|Hoffm|Roth|Willd|DC|Hartm|Huds|Lam|Schreb|Sm|Weber|Mert|Koch|Rchb|Fisch|Trin|Retz|Wahlenb|"
    r"Pers|Fr|Michx|Bernh|Moench|Gaertn|Raf)\.?(?=\s|$|,|\))"
)
_LAT_BINOM_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+(?:\((?:[A-Z][a-z]+)\)\s+)?([a-z][a-z\-]{2,})\b")
_LAT_GENUS_SP_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+(?:sp|spp)\.?(?=\s|$|\)|,)")
_GENERIC = {"вид", "особь", "след", "встреча", "первый", "последний", "прилет", "отлет", "начало",
            "массовый", "зацветание", "цветение", "песня", "кукование", "регистрация"}


def clean_name(s: str) -> str:
    """Убрать авторов таксона, годы, «s.l.», скобки вокруг латыни."""
    s = re.sub(r"\(\s*[A-Z][a-zA-Z]+,?\s*\d{4}\s*\)", " ", s)  # (Linnaeus, 1758)
    s = re.sub(r"\b\d{4}\b", " ", s)
    s = re.sub(r"\bs\.\s?l\.", " ", s)
    s = re.sub(r"\bex\b", " ", s)
    s = _AUTHOR_RE.sub(" ", s)
    s = re.sub(r"\(\s*[A-Z]\.?\s*\)", " ", s)  # (L.)
    s = s.replace("(", " ").replace(")", " ")
    return fold(s).strip(" ,.;")


@dataclass
class TaxonMatch:
    taxon_id: int | None = None
    method: str | None = None  # exact/latin/lemma/head/fuzzy/genus
    ambiguous: bool = False
    candidates: list[int] = field(default_factory=list)
    score: float = 0.0

    @property
    def unmatched(self) -> bool:
        return self.taxon_id is None and not self.ambiguous


class TaxonIndex:
    def __init__(self, d: Dictionaries):
        self.d = d
        self.exact: dict[str, set[int]] = {}
        self.lat_binom: dict[str, set[int]] = {}
        self.genus: dict[str, set[int]] = {}
        self.lemma_seqs: list[tuple[tuple[str, ...], int]] = []
        self.head: dict[str, set[int]] = {}
        self.fuzzy_names: list[str] = []
        self.fuzzy_ids: list[int] = []
        names = list(d.taxon_names)
        for t in d.taxa:  # принятые имена — даже если их нет в taxon_names
            names.append({"taxon_id": t["id"], "name": t["name_lat"], "lang": "lat"})
            if t.get("name_ru"):
                names.append({"taxon_id": t["id"], "name": t["name_ru"], "lang": "ru"})
            if t.get("genus"):
                self.genus.setdefault(fold(t["genus"]), set()).add(t["id"])
        for n in names:
            tid, name = n["taxon_id"], n["name"]
            c = clean_name(name)
            if not c:
                continue
            self.exact.setdefault(c, set()).add(tid)
            self.fuzzy_names.append(c)
            self.fuzzy_ids.append(tid)
            if n.get("lang") == "lat":
                m = _LAT_BINOM_RE.search(clean_name_keep_case(name))
                if m:
                    self.lat_binom.setdefault(fold(f"{m.group(1)} {m.group(2)}"), set()).add(tid)
            else:
                seq = lemmas(c)
                if seq:
                    self.lemma_seqs.append((seq, tid))
                    # «Лебедь-кликун» → голова «лебедь»
                    for w in seq:
                        if "-" in w:
                            self.head.setdefault(w.split("-")[0], set()).add(tid)
                    if len(seq) == 1:
                        self.head.setdefault(seq[0], set()).add(tid)
                    else:
                        # «Бурый медведь» → голова «медведь»
                        nouns = [w for w in seq if _is_noun(w)]
                        for w in nouns[:1]:
                            self.head.setdefault(w, set()).add(tid)
        self.lemma_seqs.sort(key=lambda x: -len(x[0]))

    def match(self, raw: str | None) -> TaxonMatch:
        if not raw or not raw.strip():
            return TaxonMatch()
        c = clean_name(raw)
        # 1. точное
        if c in self.exact:
            return _pick(self.exact[c], "exact", 100)
        # 2. латынь
        keep = clean_name_keep_case(raw)
        for m in _LAT_BINOM_RE.finditer(keep):
            key = fold(f"{m.group(1)} {m.group(2)}")
            if key in self.lat_binom:
                return _pick(self.lat_binom[key], "latin", 100)
        # 3. по леммам: самое длинное название справочника, входящее подряд
        seq = lemmas(c)
        best: dict[int, int] = {}
        for nseq, tid in self.lemma_seqs:
            if len(nseq) <= len(seq) and _contains(seq, nseq):
                best[tid] = max(best.get(tid, 0), len(nseq))
        if best:
            top = max(best.values())
            ids = {t for t, n in best.items() if n == top}
            # однословное совпадение по родовому слову (например «медведь» из варианта) — доверяем,
            # только если это вариант названия, а не «голова» составного
            return _pick(ids, "lemma", 95)
        # 2б. латынь — род («Salix sp.», «Vaccinium» без эпитета)
        for m in list(_LAT_BINOM_RE.finditer(keep)) + list(_LAT_GENUS_SP_RE.finditer(keep)):
            g = fold(m.group(1))
            if g in self.genus:
                r = _pick(self.genus[g], "genus", 80)
                r.ambiguous = True
                return r
        # 4. по «голове» русского названия
        heads = {w for w in seq if w not in _GENERIC}
        cands: set[int] = set()
        for w in heads:
            cands |= self.head.get(w, set())
        if cands:
            r = _pick(cands, "head", 75)
            r.ambiguous = True
            return r
        # 5. нечётко — по частям (русская часть и латинская часть отдельно)
        parts = [p for p in re.split(r"[,;]|\s{2,}", c) if p.strip()] or [c]
        lat = " ".join(m.group(0) for m in _LAT_BINOM_RE.finditer(keep))
        if lat:
            parts.append(fold(lat))
        best_score, best_ids = 0.0, set()
        for p in parts:
            p = p.strip()
            if len(p) < 4:
                continue
            for name, score, idx in process.extract(p, self.fuzzy_names, scorer=fuzz.ratio, limit=5):
                if score >= 90:
                    tid = self.fuzzy_ids[idx]
                    if score > best_score:
                        best_score, best_ids = score, {tid}
                    elif score == best_score:
                        best_ids.add(tid)
        if best_ids:
            return _pick(best_ids, "fuzzy", best_score)
        return TaxonMatch()


def clean_name_keep_case(s: str) -> str:
    s = re.sub(r"\(\s*[A-Z][a-zA-Z]+,?\s*\d{4}\s*\)", " ", s)
    s = _AUTHOR_RE.sub(" ", s)
    s = s.replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", s)


def _pick(ids: set[int], method: str, score: float) -> TaxonMatch:
    ids = sorted(ids)
    if len(ids) == 1:
        return TaxonMatch(ids[0], method, False, ids, score)
    return TaxonMatch(None, method, True, ids, score)


def _contains(seq: tuple[str, ...], sub: tuple[str, ...]) -> bool:
    n = len(sub)
    return any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


@lru_cache(maxsize=10_000)
def _is_noun(w: str) -> bool:
    from .text import _morph

    if not re.search(r"[а-я]", w):
        return False
    p = _morph().parse(w)[0]
    return "NOUN" in p.tag


@lru_cache(maxsize=1)
def default_index() -> TaxonIndex:
    return TaxonIndex(load())


def match_taxon(raw: str | None) -> TaxonMatch:
    return default_index().match(raw)


def taxon_group(taxon_id: int | None) -> str | None:
    t = load().taxon(taxon_id)
    return t.get("group_ru") if t else None


__all__ = ["TaxonMatch", "TaxonIndex", "match_taxon", "clean_name", "taxon_group", "lemma"]
