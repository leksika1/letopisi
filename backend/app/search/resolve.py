"""Строковые фильтры → id справочников + текстовый запасной вариант.

Справочники маленькие (десятки–тысячи строк), поэтому они читаются из БД целиком при каждом
разрешении и сопоставляются в Python теми же функциями нормализации, что и в пайплайне
(``pipeline/normalize``). Нечёткий поиск — pg_trgm в БД.

Запасной вариант (``patterns``) — регулярные выражения PostgreSQL (ARE) по «сырым» полям записи
(``object_raw``, ``observers_raw``, ``toponym_raw``, ``event_raw``…), приведённым к нижнему регистру
с ё→е. Они нужны, пока справочник неполон: у записи может быть ``taxon_id = NULL``, но
``object_raw = 'Лось Alces alces'``. Применяются только к записям без соответствующего id.
Регулярные выражения всегда передаются bind-параметром, пользовательский текст экранируется.

Порядок для вида (``resolve_taxon``):
1. точное совпадение по ``taxon_names.name`` / ``taxa.name_lat`` / ``taxa.name_ru`` (регистр, ё/е, авторы);
2. род, семейство, отряд, класс (латынь) или группа (``group_ru``) → все виды;
3. по леммам (pymorphy3; сравниваются множества всех нормальных форм слова — «буром» = «бурый»);
4. русское «родовое» слово («берёза», «ива») → все виды, чьё название начинается с него;
5. pg_trgm ``similarity ≥ 0.45`` (лучшие кандидаты).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import text

from pipeline.normalize import persons as _np
from pipeline.normalize import phenophases as _nph
from pipeline.normalize import places as _npl
from pipeline.normalize.dicts import Dictionaries
from pipeline.normalize.dicts import load as load_fixture_dicts
from pipeline.normalize.taxa import clean_name
from pipeline.normalize.text import fold, has_cyrillic, words

TRGM_TAXON = 0.45
TRGM_PLACE = 0.45
TRGM_PERSON = 0.6
MAX_PATTERNS = 200


# --------------------------------------------------------------------------- текст и регулярные выражения

def sql_fold(expr: str) -> str:
    """SQL-выражение «нижний регистр, ё→е» для колонки (NULL → '')."""
    return f"translate(lower(coalesce({expr}, '')), 'ё', 'е')"


def are_escape(s: str) -> str:
    """Экранирование для регулярных выражений PostgreSQL (ARE): всё, кроме букв, цифр и пробела."""
    return "".join(ch if (ch.isalnum() or ch == " ") else "\\" + ch for ch in s)


@lru_cache(maxsize=50_000)
def lemma_set(word: str) -> frozenset[str]:
    """Все нормальные формы слова по pymorphy3 (ё→е). Латиница — само слово в нижнем регистре."""
    w = fold(word)
    if not w:
        return frozenset()
    if not has_cyrillic(w):
        return frozenset({w})
    if "-" in w:
        parts = [p for p in w.split("-") if p]
        firsts = [min(lemma_set(p), key=len) if lemma_set(p) else p for p in parts]
        return frozenset({w, "-".join(firsts)})
    from pipeline.normalize.text import _morph

    forms = {p.normal_form.replace("ё", "е") for p in _morph().parse(w)}
    forms.add(w)
    return frozenset(forms)


@lru_cache(maxsize=50_000)
def main_lemma(word: str) -> str:
    from pipeline.normalize.text import lemma

    return lemma(word)


_RU_ENDINGS = ("ого", "его", "ому", "ему", "ыми", "ими", "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
               "ую", "юю", "ов", "ев", "ам", "ям", "ах", "ях", "ом", "ем", "ью", "ь", "й", "а", "я", "о", "е",
               "ы", "и", "у", "ю")


def stem(word: str) -> str:
    """Грубая основа слова для поиска по сырому тексту: лемма без окончания (не короче 3 букв)."""
    w = fold(word)
    if not has_cyrillic(w):
        return w
    base = main_lemma(w) if "-" not in w else w
    for end in _RU_ENDINGS:
        if base.endswith(end) and len(base) - len(end) >= 3:
            return base[: -len(end)]
    return base


def term_regex(s: str | None) -> str | None:
    """Строка → ARE, требующее все слова подряд (по основам, каждое — с начала слова).

    «берёзы пушистой» → ``\\mберез\\w*\\W+пушист``.
    """
    ws = [w for w in words(clean_name(s or "")) if len(w) >= 2]
    if not ws:
        return None
    parts = []
    for w in ws[:6]:
        if "-" in w:
            parts.append(r"\W?".join(are_escape(stem(p)) + r"\w*" for p in w.split("-") if p))
        else:
            parts.append(are_escape(stem(w)) + r"\w*")
    return r"\m" + r"\W+".join(parts)


def any_regex(patterns: list[str]) -> str | None:
    pats = [p for p in dict.fromkeys(patterns) if p][:MAX_PATTERNS]
    if not pats:
        return None
    return "|".join(f"(?:{p})" for p in pats)


# --------------------------------------------------------------------------- результат разрешения

@dataclass
class Resolved:
    """Результат разрешения строки.

    ids — найденные id справочника (пусто — не найдено); patterns — ARE для запасного текстового
    поиска; method — каким шагом найдено; label — подпись для пояснения.
    """

    query: str
    ids: list[int] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    method: str | None = None
    label: str | None = None

    @property
    def regex(self) -> str | None:
        return any_regex(self.patterns)

    @property
    def found(self) -> bool:
        return bool(self.ids)


def _rows(conn, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


# --------------------------------------------------------------------------- таксоны

def load_taxa(conn) -> tuple[list[dict], list[dict]]:
    taxa = _rows(conn, 'SELECT id, name_lat, name_ru, rank, genus, family, "order", class, group_ru FROM taxa')
    names = _rows(conn, "SELECT taxon_id, name, lang, kind FROM taxon_names")
    for t in taxa:
        names.append({"taxon_id": t["id"], "name": t["name_lat"], "lang": "lat", "kind": "accepted"})
        if t.get("name_ru"):
            names.append({"taxon_id": t["id"], "name": t["name_ru"], "lang": "ru", "kind": "accepted"})
    return taxa, names


def _seq_match(query_words: list[str], name_words: list[str]) -> bool:
    if len(query_words) != len(name_words):
        return False
    return all(lemma_set(q) & lemma_set(n) for q, n in zip(query_words, name_words))


def is_head(name_words: list[str], word: str) -> bool:
    """``word`` — «родовое» (первое) слово составного русского названия:
    «берёза» для «Береза пушистая», «лебедь» для «Лебедь-кликун»."""
    if not name_words:
        return False
    first = name_words[0]
    if len(name_words) >= 2:
        return bool(lemma_set(first) & lemma_set(word))
    if "-" in first:
        return bool(lemma_set(first.split("-")[0]) & lemma_set(word))
    return False


def _is_noun(word: str) -> bool:
    from pipeline.normalize.text import _morph

    return any("NOUN" in p.tag for p in _morph().parse(fold(word))[:2])


def taxon_label(t: dict) -> str:
    """«лось (Alces alces)» — как в примере openapi.yaml."""
    ru = t.get("name_ru")
    if ru:
        return f"{ru[:1].lower()}{ru[1:]} ({t['name_lat']})"
    return t["name_lat"]


def _taxon_patterns(names: list[dict], ids: set[int], query: str | None) -> list[str]:
    pats = [term_regex(query)] if query else []
    for n in names:
        if n["taxon_id"] in ids:
            pats.append(term_regex(n["name"]))
    return [p for p in pats if p]


def resolve_taxon(conn, s: str | None) -> Resolved:
    """Строка вида (рус./лат./синоним/род/группа, любой падеж) → набор taxon_id + запасной ARE."""
    res = Resolved(query=s or "")
    if not s or not s.strip():
        return res
    taxa, names = load_taxa(conn)
    by_id = {t["id"]: t for t in taxa}
    f = clean_name(s)

    def done(ids: set[int], method: str, patterns: list[str] | None = None) -> Resolved:
        res.ids = sorted(ids)
        res.method = method
        res.patterns = patterns if patterns is not None else _taxon_patterns(names, ids, s)
        if len(res.ids) == 1:
            res.label = taxon_label(by_id[res.ids[0]])
        elif res.ids:
            res.label = f"{s} ({len(res.ids)} видов)"
        return res

    # 1. точное
    ids = {n["taxon_id"] for n in names if clean_name(n["name"]) == f}
    if ids:
        return done(ids, "exact")
    # 2. род / семейство / отряд / класс / группа
    for key in ("genus", "family", "order", "class"):
        ids = {t["id"] for t in taxa if t.get(key) and fold(t[key]) == f}
        if ids:
            return done(ids, key)
    ids = {t["id"] for t in taxa if fold(t["group_ru"]) == f or {main_lemma(w) for w in words(f)} == {
        main_lemma(w) for w in words(t["group_ru"])}}
    if ids:
        pat = term_regex(s)
        return done(ids, "group", [pat] if pat else [])
    # 3. по леммам: название целиком, затем — самое длинное название, входящее подряд
    qw = words(f)
    ids = set()
    for n in names:
        if n["lang"] == "ru" and _seq_match(qw, words(clean_name(n["name"]))):
            ids.add(n["taxon_id"])
    if ids:
        return done(ids, "lemma")
    best, best_len = set(), 0
    for n in names:
        nw = words(clean_name(n["name"]))
        if not nw or len(nw) > len(qw):
            continue
        for i in range(len(qw) - len(nw) + 1):
            if _seq_match(qw[i:i + len(nw)], nw):
                if len(nw) > best_len:
                    best, best_len = {n["taxon_id"]}, len(nw)
                elif len(nw) == best_len:
                    best.add(n["taxon_id"])
                break
    if best and (best_len >= 2 or len(qw) <= 2):
        return done(best, "lemma")
    # 4. русское родовое слово: «берёза» → все «Береза …»
    if len(qw) == 1 and has_cyrillic(qw[0]) and _is_noun(qw[0]):
        ids = set()
        for n in names:
            nw = words(clean_name(n["name"]))
            if n["lang"] == "ru" and is_head(nw, qw[0]):
                ids.add(n["taxon_id"])
        if ids:
            return done(ids, "genus_ru")
    # 5. pg_trgm
    rows = _rows(conn, """
        SELECT taxon_id, max(similarity(translate(lower(name), 'ё', 'е'), :f)) AS s
        FROM (SELECT taxon_id, name FROM taxon_names
              UNION ALL SELECT id, name_lat FROM taxa
              UNION ALL SELECT id, name_ru FROM taxa WHERE name_ru IS NOT NULL) n
        WHERE similarity(translate(lower(name), 'ё', 'е'), :f) >= :thr
        GROUP BY taxon_id ORDER BY s DESC LIMIT 20""", f=f, thr=TRGM_TAXON)
    if rows:
        top = rows[0]["s"]
        return done({r["taxon_id"] for r in rows if r["s"] >= top - 0.05}, "trgm")
    pat = term_regex(s)
    res.patterns = [pat] if pat else []
    res.method = "text"
    return res


def taxon_by_id(conn, taxon_id: int) -> Resolved:
    """taxon_id → его названия как запасной текстовый вариант (для записей с taxon_id NULL)."""
    taxa, names = load_taxa(conn)
    t = next((x for x in taxa if x["id"] == taxon_id), None)
    res = Resolved(query=str(taxon_id), ids=[taxon_id], method="id")
    if t:
        res.label = taxon_label(t)
        res.patterns = _taxon_patterns(names, {taxon_id}, None)
    return res


# --------------------------------------------------------------------------- люди

_MALE_ENDINGS = ("", "у", "ым", "ом", "е")
_FEMALE_ENDINGS = ("а", "ой", "ою", "у")


def surname_regex(surname: str) -> str:
    """ARE для фамилии во всех падежах, но без смешения мужской и женской формы.

    «Сметанников» → сметанников(у|ым|ом|е)? — «Сметанникова» (женская фамилия) не подходит;
    «Макарова» → макаров(а|ой|ою|у). Сравнение — по fold-строке (нижний регистр, ё→е).
    """
    s = fold(surname)
    if re.search(r"(ова|ева|ина|ына)$", s):
        stem_, ends = s[:-1], _FEMALE_ENDINGS
    elif re.search(r"(ов|ев|ин|ын)$", s):
        stem_, ends = s, _MALE_ENDINGS
    elif re.search(r"(ский|цкий)$", s):
        stem_, ends = s[:-2], ("ий", "ого", "ому", "им", "ом")
    elif re.search(r"(ская|цкая)$", s):
        stem_, ends = s[:-2], ("ая", "ой", "ую")
    else:
        stem_, ends = s, ("",)
    alt = "|".join(are_escape(e) for e in ends if e)
    tail = f"(?:{alt})" + ("?" if "" in ends else "") if alt else ""
    return r"\m" + are_escape(stem_) + tail + r"\M"


def split_people(values: list[str] | str | None) -> list[str]:
    """['Макарова и Сметанников'] → ['Макарова', 'Сметанников'] (фамилии, без инициалов)."""
    if not values:
        return []
    out: list[str] = []
    for v in [values] if isinstance(values, str) else values:
        refs = _np.parse_people(v)
        found = [r.surname for r in refs]
        if not found:  # строчные буквы, латиница и т. п.
            found = [w for w in re.split(r",|;|\s+и\s+|\s+&\s+|\s+", v) if len(w.strip(" .")) >= 3]
        for s in found:
            s = s.strip(" .")
            if s and s not in out:
                out.append(s)
    return out


def load_persons(conn) -> list[dict]:
    return _rows(conn, "SELECT id, surname, initials, full_raw, years_active, year_start, year_end, still_working "
                       "FROM persons")


def find_person_ids(persons: list[dict], surname: str, conn=None) -> tuple[list[int], str | None]:
    """Фамилия (любой падеж) → id каталога. Точное совпадение приоритетнее падежных вариантов,
    поэтому «Сметанников» не находит «Сметанникову» и наоборот."""
    by: dict[str, list[int]] = {}
    for p in persons:
        by.setdefault(fold(p["surname"]), []).append(p["id"])
    for i, v in enumerate(_np._variants(surname)):
        if v in by:
            return sorted(by[v]), "exact" if i == 0 else "case"
    if conn is not None and len(surname) >= 4:
        rows = _rows(conn, """SELECT id, similarity(translate(lower(surname), 'ё', 'е'), :s) AS s FROM persons
                              WHERE similarity(translate(lower(surname), 'ё', 'е'), :s) >= :thr
                              ORDER BY s DESC LIMIT 5""", s=fold(surname), thr=TRGM_PERSON)
        if rows:
            top = rows[0]["s"]
            return sorted(r["id"] for r in rows if r["s"] >= top - 0.01), "trgm"
    return [], None


def resolve_persons(conn, values: list[str] | str | None) -> Resolved:
    """Фамилии → person_ids + ARE для observers_raw / pub_authors / sections.authors_raw."""
    surnames = split_people(values)
    res = Resolved(query=", ".join(surnames))
    if not surnames:
        return res
    persons = load_persons(conn)
    by_id = {p["id"]: p for p in persons}
    ids: list[int] = []
    pats: list[str] = []
    labels: list[str] = []
    methods: list[str] = []
    for s in surnames:
        pids, how = find_person_ids(persons, s, conn)
        for pid in pids:
            if pid not in ids:
                ids.append(pid)
            pats.append(surname_regex(by_id[pid]["surname"]))
        if pids:
            labels.append(", ".join(by_id[p]["surname"] for p in pids))
            methods.append(how or "")
        else:
            pats.append(surname_regex(s))
            labels.append(f"{s} (нет в каталоге)")
            methods.append("text")
    res.ids, res.patterns, res.label, res.method = ids, pats, ", ".join(labels), ",".join(methods)
    return res


# --------------------------------------------------------------------------- места

def load_places(conn) -> tuple[list[dict], list[dict]]:
    places = _rows(conn, "SELECT id, name, category, lat, lon, coords_source FROM places")
    pnames = _rows(conn, "SELECT id, place_id, name, kind FROM place_names")
    return places, pnames


def place_index(places: list[dict], pnames: list[dict]) -> _npl.PlaceIndex:
    return _npl.PlaceIndex(Dictionaries(places=places, place_names=pnames))


_PLACE_ENDINGS = ("а", "я", "ы", "и", "е", "у", "ю", "ой", "ей", "ою", "ь", "ем", "ом")


def place_word_regex(name: str) -> str | None:
    """Название места → ARE по значимым словам: «Калкупя» → \\mкалкуп(?:а|я|и|…)?\\M."""
    ws = [w for w in re.findall(r"[а-яa-z]+(?:-[а-яa-z]+)*", fold(name))
          if w not in _npl.GENERIC and len(w) >= 3]
    if not ws:
        return None
    parts = []
    for w in ws:
        if w.endswith(_npl._FINNISH) or len(w) < 5:
            parts.append(are_escape(w) + r"\M")
        else:
            st = w[:-1] if w[-1] in "аяыиеоуюь" else w
            parts.append(are_escape(st) + "(?:" + "|".join(_PLACE_ENDINGS) + r")?\M")
    return r"\m" + r"\W+".join(parts)


def place_patterns(places: list[dict], pnames: list[dict], ids: set[int]) -> list[str]:
    pats = [place_word_regex(p["name"]) for p in places if p["id"] in ids]
    pats += [place_word_regex(n["name"]) for n in pnames if n["place_id"] in ids]
    return [p for p in pats if p]


def resolve_place(conn, s: str | None) -> Resolved:
    res = Resolved(query=s or "")
    if not s or not s.strip():
        return res
    places, pnames = load_places(conn)
    by_id = {p["id"]: p for p in places}
    m = place_index(places, pnames).match(s)
    ids: set[int] = set()
    if m.place_id:
        ids, res.method = {m.place_id}, m.method
    else:
        rows = _rows(conn, """
            SELECT place_id, max(similarity(translate(lower(name), 'ё', 'е'), :f)) AS s
            FROM (SELECT place_id, name FROM place_names UNION ALL SELECT id, name FROM places) n
            WHERE similarity(translate(lower(name), 'ё', 'е'), :f) >= :thr
            GROUP BY place_id ORDER BY s DESC LIMIT 10""", f=fold(s), thr=TRGM_PLACE)
        if rows:
            top = rows[0]["s"]
            ids, res.method = {r["place_id"] for r in rows if r["s"] >= top - 0.05}, "trgm"
    res.ids = sorted(ids)
    pats = place_patterns(places, pnames, ids)
    own = place_word_regex(s)
    res.patterns = pats + ([own] if own else [])
    if not ids:
        res.method = "text"
    res.label = ", ".join(by_id[i]["name"] for i in res.ids) if res.ids else s
    return res


def place_by_id(conn, place_id: int) -> tuple[dict | None, list[str]]:
    places, pnames = load_places(conn)
    p = next((x for x in places if x["id"] == place_id), None)
    return p, place_patterns(places, pnames, {place_id}) if p else []


# --------------------------------------------------------------------------- фенофазы

def load_phenophases(conn) -> list[dict]:
    return _rows(conn, "SELECT id, code, name_ru, applies_to, bbch, ord FROM phenophases ORDER BY ord, id")


def _ph_by_code(rows: list[dict], applies: str, code: str) -> dict | None:
    return next((r for r in rows if r["applies_to"] == applies and r["code"] == code), None)


def resolve_phenophase(conn, s: str | None, group: str | None = None) -> Resolved:
    """«зацветание» / «цветение» / «фаза 5» / «10а» / «прилёт» → phenophase_id (+ ARE по event_raw)."""
    res = Resolved(query=s or "")
    if not s or not s.strip():
        return res
    rows = load_phenophases(conn)
    f = fold(s)
    code = re.sub(r"^(1\d)([ab])$", lambda m: m.group(1) + {"a": "а", "b": "б"}[m.group(2)], f)
    hit = next((r for r in rows if fold(r["code"]) == code), None)
    if hit is None:
        ec = _nph.explicit_code(s)
        if ec:
            hit = _ph_by_code(rows, "plants", ec)
    if hit is None:
        hit = next((r for r in rows if r["name_ru"] and fold(r["name_ru"]) == f), None)
    if hit is None:
        pid, _frag = _nph.match_phenophase(s, group=group)
        fx = load_fixture_dicts().phenophase(pid) if pid else None
        if fx:  # id фикстур → код → id в БД
            hit = _ph_by_code(rows, fx["applies_to"], fx["code"])
    if hit is None:
        r2 = _rows(conn, """SELECT id, similarity(translate(lower(name_ru), 'ё', 'е'), :f) AS s FROM phenophases
                            WHERE name_ru IS NOT NULL AND similarity(translate(lower(name_ru), 'ё', 'е'), :f) >= 0.3
                            ORDER BY s DESC LIMIT 1""", f=f)
        if r2:
            hit = next(r for r in rows if r["id"] == r2[0]["id"])
            res.method = "trgm"
    if hit is not None:
        res.ids = [hit["id"]]
        res.method = res.method or "rules"
        res.label = phenophase_label(hit)
    pat = term_regex(s)
    res.patterns = [pat] if pat else []
    return res


def phenophase_label(r: dict) -> str:
    return f"{r['code']} — {r['name_ru']}" if r.get("name_ru") else str(r["code"])
