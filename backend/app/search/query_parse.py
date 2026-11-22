"""POST /api/query/parse: запрос обычными словами → {filters, explanation, warnings}.

Две ступени.

(а) Правила без LLM (детерминированно, покрывают 4 приёмочных запроса жюри):
- годы и диапазоны: «в 1995-1996», «за 1995, 1996 и 2022 годы», «с 1995 по 1996», «в 90-е»;
  предлог решает, что это: «в/во/с …» → годы наблюдения (``years``), «за …» или упоминание
  летописи/книги/тома рядом → годы книг (``doc_years``) — см. contracts/README.md, «Решения», п. 8;
- опечатка в годе: если года нет ни среди лет книг (``documents.year``), ни среди лет наблюдений,
  а в пределах ±2 есть год книги — он подставляется, замена сообщается в ``warnings``
  («2022 → 2020: книги за 2022 год нет, использован том 2020 года»);
- «по хронологии», «по дате» → ``sort=date_from, order=asc``; «сначала новые» → ``desc``;
- «публикации/статьи/литература» → ``record_type=publication``; «наблюдения» → ``observation``;
- фамилии — по каталогу ``persons`` (падежи, «Сметанников» ≠ «Сметанникова»);
- виды — по ``taxon_names`` (леммы слов, n-граммы; род → строка ``taxon``);
- фенофазы — по ключевым словам («зацветание», «цветение», «прилёт» …) через pipeline/normalize/phenophases;
- топонимы — по каталогу мест; «в заповеднике „Пасвик“» означает всю территорию и фильтром не ставится.

(б) Если после правил остались значимые несопоставленные слова — LLM (Yandex AI Studio,
клиент ``pipeline/extract/llm.py``: те же ключи ``YC_API_KEY``/``YC_FOLDER_ID`` из окружения или .env)
с JSON-схемой фильтров, temperature 0, кэш на диске и в памяти. Без ключа или при
``LLM_BACKEND=mock`` — только правила. Значения от LLM снова проходят через справочники; поля,
найденные правилами, LLM не перезаписывает.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from pipeline.normalize import phenophases as _nph
from pipeline.normalize.dicts import load as load_fixture_dicts
from pipeline.normalize.text import fold, has_cyrillic

from . import resolve as R
from .filters import SearchFilters

log = logging.getLogger("backend.search.query_parse")

PARSER_VERSION = "qp-1"
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_YEAR = r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)"
_YEAR_GROUP_RE = re.compile(_YEAR + r"(?:\s*(?:,|и|-|–|—|по|до)\s*" + _YEAR.replace("(1", "(?:1") + r")*")
_DECADE_RE = re.compile(r"(?<!\d)(\d{2}|1[89]\d0|20\d0)\s*-?\s*(?:е|х|ые|ых)(?![А-Яа-яЁё])")
_DOC_CONTEXT_RE = re.compile(r"летопис|книг|том[аеу]?\b|пдф|pdf|скан", re.IGNORECASE)

STOP_LEMMAS = {
    # служебные слова
    "в", "во", "за", "о", "об", "обо", "на", "по", "для", "и", "или", "с", "со", "из", "от", "до", "у", "к",
    "а", "также", "этот", "тот", "весь", "все", "всё", "какой", "как", "где", "когда", "который", "мой",
    "каждый", "между", "при", "про", "без", "не", "ли", "же", "то", "год", "гг", "г",
    # «оболочка» запроса
    "создать", "сделать", "составить", "показать", "найти", "вывести", "выдать", "сформировать", "получить",
    "нужно", "нужный", "надо", "хотеть", "пожалуйста", "сводный", "таблица", "список", "перечень", "данные",
    "данный", "запись", "информация", "сведение", "наблюдение", "регистрация", "зарегистрировать",
    "отметить", "отмеченный", "расположить", "упорядочить", "отсортировать", "сортировка", "порядок",
    "хронология", "хронологический", "дата", "время", "период", "течение", "основание", "основа",
    "пдф-версия", "пдф", "версия", "pdf", "сканировать", "сканированный", "летопись", "природа", "книга",
    "том", "заповедник", "территория", "публикация", "статья", "литература", "библиография", "вид",
    "сначала", "новый", "старый", "убывание", "возрастание", "обратный",
}

KEY_PHENO_RE = re.compile(
    r"зацвет|цветени|цветущ|бутониз|отцвет|листопад|пожелт|созревани|плодоношени|прил[её]т|отл[её]т|"
    r"песн|кукован|гнезд|выводк|линьк|спячк|берлог", re.IGNORECASE)

RT_LABEL = {"observation": "наблюдения", "env_measurement": "измерения среды", "publication": "публикации"}
SORT_LABEL = {"date_from": "по дате (хронологически)", "obs_year": "по году", "object_raw": "по объекту",
              "toponym_raw": "по месту", "confidence": "по достоверности", "status": "по статусу", "id": "по id",
              "created_at": "по дате создания", "updated_at": "по дате изменения"}


@dataclass
class Token:
    w: str
    start: int
    end: int
    used: bool = False

    @property
    def f(self) -> str:
        return fold(self.w)

    @property
    def cap(self) -> bool:
        return self.w[:1].isupper()


@dataclass
class ParseState:
    text: str
    tokens: list[Token]
    filters: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    used_spans: list[tuple[int, int]] = field(default_factory=list)
    llm_used: bool = False

    def use_span(self, a: int, b: int) -> None:
        self.used_spans.append((a, b))
        for t in self.tokens:
            if t.start >= a and t.end <= b:
                t.used = True

    def free(self) -> list[Token]:
        return [t for t in self.tokens if not t.used]


# --------------------------------------------------------------------------- годы

def _years_in_group(s: str) -> list[int]:
    nums = [int(x) for x in re.findall(_YEAR, s)]
    seps = re.findall(r"(?<=\d)\s*(,|и|-|–|—|по|до)\s*(?=\d)", s)
    out: list[int] = []
    for i, y in enumerate(nums):
        if i > 0 and seps[i - 1] in ("-", "–", "—", "по", "до") and nums[i - 1] <= y <= nums[i - 1] + 300:
            out.extend(range(nums[i - 1] + 1, y + 1))
        else:
            out.append(y)
    return sorted(set(out))


def _target_for(text_: str, start: int) -> str:
    before = text_[max(0, start - 60):start]
    m = re.search(r"([А-Яа-яЁё]+)\s*$", before)
    prep = fold(m.group(1)) if m else ""
    if prep == "за" or _DOC_CONTEXT_RE.search(before[-40:]):
        return "doc_years"
    return "years"


def _parse_years(st: ParseState, book_years: set[int], obs_years: set[int]) -> None:
    found: dict[str, list[int]] = {"years": [], "doc_years": []}
    literal: set[tuple[str, int]] = set()  # годы, написанные списком (не диапазон/десятилетие), — их можно поправить
    for m in _YEAR_GROUP_RE.finditer(st.text):
        ys = _years_in_group(m.group(0))
        tgt = _target_for(st.text, m.start())
        found[tgt].extend(ys)
        if not re.search(r"\d\s*(?:-|–|—|по|до)\s*\d", m.group(0)):
            literal.update((tgt, y) for y in ys)
        st.use_span(m.start(), m.end())
    for m in _DECADE_RE.finditer(st.text):
        v = m.group(1)
        if len(v) == 2:
            base = (1900 if int(v) >= 30 else 2000) + int(v)
        else:
            base = int(v)
        if base % 10:
            continue
        tgt = _target_for(st.text, m.start())
        found[tgt].extend(range(base, base + 10))
        st.use_span(m.start(), m.end())
        st.notes.append(f"{v}-е → {base}–{base + 9}")
    for tgt, ys in found.items():
        if not ys:
            continue
        fixed: list[int] = []
        for y in sorted(set(ys)):
            ok = y in book_years if tgt == "doc_years" else (y in book_years or y in obs_years)
            if not ok and book_years and (tgt, y) in literal:
                near = min(book_years, key=lambda b: (abs(b - y), -b))
                if abs(near - y) <= 2:
                    st.warnings.append(f"{y} → {near}: книги за {y} год нет, использован том {near} года")
                    y = near
            if y not in fixed:
                fixed.append(y)
        st.filters[tgt] = sorted(fixed)


# --------------------------------------------------------------------------- сортировка и тип записи

def _parse_sort_and_type(st: ParseState) -> None:
    low = fold(st.text)
    if re.search(r"сначала нов|по убывани|обратн\w* (хронолог|порядк)|от поздн", low):
        st.filters["sort"], st.filters["order"] = "date_from", "desc"
    elif re.search(r"хронолог|по дат|по времени|по порядку дат|от ранн", low):
        st.filters["sort"], st.filters["order"] = "date_from", "asc"
    for t in st.tokens:
        if re.match(r"хронолог|сначала|убывани|обратн", t.f):
            t.used = True
    if re.search(r"публикац|стать[яиейю]|литератур|библиограф", low):
        st.filters["record_type"] = "publication"
    elif re.search(r"измерени|метео|температур|погод", low):
        st.filters["record_type"] = "env_measurement"
    elif re.search(r"наблюдени", low):
        st.filters["record_type"] = "observation"
    for t in st.tokens:
        if re.match(r"публикац|стать[яиейю]|литератур|библиограф|измерени|метео|наблюдени", t.f):
            t.used = True
    if st.filters.get("record_type") == "publication" and "sort" not in st.filters:
        st.filters["sort"], st.filters["order"] = "obs_year", "asc"


# --------------------------------------------------------------------------- люди

def _parse_persons(st: ParseState, persons: list[dict]) -> None:
    by_id = {p["id"]: p for p in persons}
    names: list[str] = []
    for t in st.tokens:
        if t.used or not t.cap or not has_cyrillic(t.w) or len(t.w) < 4:
            continue
        ids, _how = R.find_person_ids(persons, t.w)
        if not ids:
            continue
        t.used = True
        for pid in ids:
            s = by_id[pid]["surname"]
            if s not in names:
                names.append(s)
    if names:
        st.filters["person"] = names
        st.labels["person"] = ", ".join(names)


# --------------------------------------------------------------------------- виды

def _parse_taxa(st: ParseState, conn) -> None:
    taxa, names = R.load_taxa(conn)
    by_id = {t["id"]: t for t in taxa}
    name_words = [(n, R.words(R.clean_name(n["name"]))) for n in names]
    hits: list[tuple[int, int, set[int], str]] = []  # (start_idx, len, ids, method)
    free_idx = [i for i, t in enumerate(st.tokens) if not t.used and fold(t.w) not in STOP_LEMMAS]
    toks = st.tokens
    taken: set[int] = set()
    for n in range(4, 0, -1):
        for k in range(len(toks) - n + 1):
            idx = list(range(k, k + n))
            if any(i in taken or i not in free_idx for i in idx):
                continue
            qw = [toks[i].w for i in idx]
            if n == 1 and R.main_lemma(qw[0]) in STOP_LEMMAS:
                continue
            ids = {nm["taxon_id"] for nm, nw in name_words if R._seq_match(qw, nw)}
            method = "lemma"
            if not ids and n == 1:
                w = qw[0]
                if not has_cyrillic(w):
                    ids = {t["id"] for t in taxa if t.get("genus") and fold(t["genus"]) == fold(w)}
                    method = "genus"
                elif R._is_noun(w) and len(w) >= 4:
                    ids = {nm["taxon_id"] for nm, nw in name_words
                           if nm["lang"] == "ru" and R.is_head(nw, w)}
                    method = "genus_ru"
            if ids:
                hits.append((k, n, ids, method))
                taken.update(idx)
    if not hits:
        return
    hits.sort()
    k, n, ids, method = hits[0]
    for i in range(k, k + n):
        toks[i].used = True
    phrase = " ".join(toks[i].w for i in range(k, k + n))
    if len(ids) == 1:
        tid = next(iter(ids))
        st.filters["taxon_id"] = tid
        st.labels["taxon"] = R.taxon_label(by_id[tid])
    else:
        st.filters["taxon"] = phrase
        st.labels["taxon"] = f"{phrase} ({len(ids)} видов: " + ", ".join(
            (by_id[i].get("name_ru") or by_id[i]["name_lat"]) for i in sorted(ids)) + ")"
    st.labels["_taxon_group"] = next(iter({by_id[i]["group_ru"] for i in ids})) if len(
        {by_id[i]["group_ru"] for i in ids}) == 1 else ""
    if len(hits) > 1:
        others = [" ".join(toks[i].w for i in range(h[0], h[0] + h[1])) for h in hits[1:]]
        for h in hits[1:]:
            for i in range(h[0], h[0] + h[1]):
                toks[i].used = True
        st.warnings.append("Фильтр поддерживает один вид: учтён «" + phrase + "», не учтены: " + ", ".join(others))


# --------------------------------------------------------------------------- фенофазы

def _parse_phenophase(st: ParseState, conn) -> None:
    toks = st.tokens
    for i, t in enumerate(toks):
        if t.used or not KEY_PHENO_RE.search(t.f):
            continue
        phrase = t.w
        if i > 0 and not toks[i - 1].used and re.match(r"массов|начал|первы|полн|окончан|конец|последн", toks[i - 1].f):
            phrase = toks[i - 1].w + " " + t.w
            toks[i - 1].used = True
        group = st.labels.get("_taxon_group") or None
        pid, _frag = _nph.match_phenophase(phrase, group=group)
        fx = load_fixture_dicts().phenophase(pid) if pid else None
        row = None
        if fx:
            row = conn.execute(text("SELECT id, code, name_ru FROM phenophases WHERE applies_to = :a AND code = :c"),
                               {"a": fx["applies_to"], "c": fx["code"]}).mappings().first()
        t.used = True
        if row:
            st.filters["phenophase_id"] = int(row["id"])
            st.labels["phenophase"] = R.phenophase_label(dict(row))
            return
        st.warnings.append(f"Фенофаза «{phrase}» не найдена в справочнике")
        return


# --------------------------------------------------------------------------- места

def _parse_place(st: ParseState, conn) -> None:
    places, pnames = R.load_places(conn)
    idx = R.place_index(places, pnames)
    by_id = {p["id"]: p for p in places}
    toks = st.tokens
    for i, t in enumerate(toks):
        if t.used or not t.cap or i == 0 or len(t.w) < 3:
            continue
        phrase = t.w
        if i > 0 and fold(toks[i - 1].w) in R._npl.GENERIC:
            phrase = toks[i - 1].w + " " + t.w
        m = idx.match(phrase)
        if not m.place_id or m.method == "reserve":
            continue
        t.used = True
        if m.place_id == idx.reserve_id:
            st.notes.append("территория: весь заповедник «Пасвик» (фильтр по месту не ставится)")
            continue
        if "place_id" not in st.filters:
            st.filters["place_id"] = m.place_id
            st.labels["place"] = by_id[m.place_id]["name"]


# --------------------------------------------------------------------------- LLM

_LLM_CACHE: dict[str, dict] = {}

LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "taxon": {"type": ["string", "null"], "description": "вид/род/группа, как в запросе, в именительном падеже"},
        "person": {"type": ["array", "null"], "items": {"type": "string"}, "description": "фамилии наблюдателей"},
        "place": {"type": ["string", "null"], "description": "топоним"},
        "phenophase": {"type": ["string", "null"], "description": "фенологическое явление: цветение, прилёт …"},
        "years": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "годы наблюдения"},
        "doc_years": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "годы книг летописи"},
        "record_type": {"type": ["string", "null"], "enum": ["observation", "env_measurement", "publication", None]},
        "group": {"type": ["string", "null"], "enum": [
            "сосудистые растения", "мхи", "лишайники", "грибы", "птицы", "млекопитающие", "рыбы",
            "амфибии и рептилии", "насекомые", "прочие беспозвоночные", None]},
        "indicator": {"type": ["string", "null"], "description": "измеряемый показатель (высота снега …)"},
        "date_from": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "date_to": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "q": {"type": ["string", "null"], "description": "прочие значимые слова для текстового поиска"},
    },
    "required": ["taxon", "person", "place", "phenophase", "years", "doc_years", "record_type", "group",
                 "indicator", "date_from", "date_to", "q"],
    "additionalProperties": False,
}

LLM_SYSTEM = (
    "Ты переводишь запрос пользователя к базе наблюдений «Летописи природы» заповедника «Пасвик» "
    "в JSON-фильтры строго по схеме. Не придумывай значений: если чего-то нет в запросе — null. "
    "Названия видов, мест и фамилии пиши в именительном падеже так, как они названы в запросе. "
    "years — годы самих наблюдений («в 1995 году»), doc_years — годы книг летописи («летопись за 2020 год»). "
    "record_type: observation — наблюдения видов и явлений, env_measurement — погода, снег, вода, "
    "publication — публикации/статьи. Ответ — только JSON."
)


def _llm_client():
    """Клиент LLM ядра или None (нет ключа, LLM_BACKEND=mock, нет пакета openai)."""
    try:
        from pipeline.extract.llm import LLMClient, LLMUnavailable, load_dotenv
    except Exception:  # noqa: BLE001 — нет openai/pydantic-моделей ядра
        return None
    load_dotenv()
    client = LLMClient()
    if client.backend == "mock":
        return None
    try:
        client._openai()
    except (LLMUnavailable, ImportError):
        return None
    return client


def _llm_call(query: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """Один запрос к LLM (temperature 0, json_schema → json_object), с кэшем. None — LLM недоступен."""
    client = _llm_client()
    if client is None:
        return None
    user = json.dumps({"query": query, "context": context}, ensure_ascii=False)
    key = hashlib.sha256((client.model + PARSER_VERSION + LLM_SYSTEM + user).encode("utf-8")).hexdigest()
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]
    cdir = client.cache_dir / "query_parse"
    path = cdir / f"{key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))["response"]
            _LLM_CACHE[key] = data
            return data
        except (ValueError, KeyError):
            pass
    from pipeline.extract.llm import _strip_fences

    messages = [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": user}]
    oa = client._openai()
    content = None
    for fmt in ({"type": "json_schema", "json_schema": {"name": "search_filters", "schema": LLM_SCHEMA}},
                {"type": "json_object"}):
        try:
            r = oa.chat.completions.create(model=client.model, messages=messages, temperature=0,
                                           response_format=fmt, max_tokens=800)
            content = r.choices[0].message.content or ""
            break
        except Exception as e:  # noqa: BLE001 — формат не поддержан / сеть: пробуем проще, затем сдаёмся
            log.info("query_parse LLM: %s", type(e).__name__)
            continue
    if content is None:
        return None
    try:
        data = json.loads(_strip_fences(content))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    _LLM_CACHE[key] = data
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": client.model, "version": PARSER_VERSION, "response": data},
                                   ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def _merge_llm(st: ParseState, conn, data: dict[str, Any]) -> None:
    f = st.filters
    if data.get("taxon") and "taxon_id" not in f and "taxon" not in f:
        res = R.resolve_taxon(conn, str(data["taxon"]))
        if len(res.ids) == 1:
            f["taxon_id"] = res.ids[0]
        else:
            f["taxon"] = str(data["taxon"])
        st.labels["taxon"] = res.label or str(data["taxon"])
    if data.get("person") and "person" not in f:
        res = R.resolve_persons(conn, [str(x) for x in data["person"] if x])
        if res.query:
            f["person"] = [s.strip() for s in res.query.split(",") if s.strip()]
            st.labels["person"] = res.label or res.query
    if data.get("place") and "place_id" not in f and "place" not in f:
        res = R.resolve_place(conn, str(data["place"]))
        places, _ = R.load_places(conn)
        reserve = next((p["id"] for p in places if fold(p["name"]) == "пасвик"), None)
        if res.ids == [reserve]:
            st.notes.append("территория: весь заповедник «Пасвик» (фильтр по месту не ставится)")
        elif len(res.ids) == 1:
            f["place_id"] = res.ids[0]
            st.labels["place"] = res.label or str(data["place"])
        else:
            f["place"] = str(data["place"])
            st.labels["place"] = str(data["place"])
    if data.get("phenophase") and "phenophase_id" not in f:
        res = R.resolve_phenophase(conn, str(data["phenophase"]))
        if res.ids:
            f["phenophase_id"] = res.ids[0]
            st.labels["phenophase"] = res.label or str(data["phenophase"])
    for k in ("years", "doc_years"):
        v = data.get(k)
        if v and k not in f:
            ys = sorted({int(y) for y in v if isinstance(y, int | float) and 1800 <= int(y) <= 2100})
            if ys:
                f[k] = ys
    for k in ("record_type", "group", "indicator", "date_from", "date_to", "q"):
        v = data.get(k)
        if v and k not in f:
            f[k] = v
    st.llm_used = True


# --------------------------------------------------------------------------- пояснение

def _explain(st: ParseState, flt: SearchFilters) -> str:
    parts: list[str] = []
    if "taxon" in st.labels:
        parts.append(f"вид: {st.labels['taxon']}")
    if flt.group:
        parts.append(f"группа: {flt.group}")
    if flt.person:
        parts.append("наблюдатели: " + ", ".join(flt.person))
    if "place" in st.labels:
        parts.append(f"место: {st.labels['place']}")
    if "phenophase" in st.labels:
        parts.append(f"фенофаза: {st.labels['phenophase']}")
    if flt.record_type:
        parts.append(f"тип записей: {RT_LABEL[flt.record_type]}")
    if flt.indicator:
        parts.append(f"показатель: {flt.indicator}")
    if flt.years:
        parts.append("годы наблюдения: " + ", ".join(map(str, flt.years)))
    if flt.doc_years:
        parts.append("годы книг (тома летописи): " + ", ".join(map(str, flt.doc_years)))
    if flt.date_from or flt.date_to:
        parts.append(f"даты: {flt.date_from or '…'} — {flt.date_to or '…'}")
    if flt.q:
        parts.append(f"текст: «{flt.q}»")
    parts.extend(st.notes)
    if flt.sort:
        s = "сортировка " + SORT_LABEL.get(flt.sort, flt.sort)
        if flt.order == "desc":
            s += ", по убыванию"
        parts.append(s)
    if not parts:
        return "Запрос не распознан: фильтры не заданы, будут показаны все записи."
    out = "; ".join(parts)
    if st.llm_used:
        out += " (часть запроса разобрана языковой моделью)"
    return out[:1].upper() + out[1:]


# --------------------------------------------------------------------------- вход

def _meaningful_leftovers(st: ParseState) -> list[str]:
    out = []
    for t in st.free():
        if len(t.w) < 3:
            continue
        if fold(t.w) in STOP_LEMMAS or R.main_lemma(t.w) in STOP_LEMMAS:
            continue
        out.append(t.w)
    return out


def parse_query(conn, text_: str, *, use_llm: bool = True) -> dict[str, Any]:
    """Текст запроса → ``{"filters": SearchFilters-JSON, "explanation": str, "warnings": [str]}``."""
    text_ = (text_ or "").strip()
    st = ParseState(text=text_, tokens=[Token(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(text_)])
    book_years = {int(r[0]) for r in conn.execute(text("SELECT DISTINCT year FROM documents WHERE year IS NOT NULL"))}
    obs_years = {int(r[0]) for r in conn.execute(
        text("SELECT DISTINCT obs_year FROM observations WHERE obs_year IS NOT NULL"))}
    _parse_years(st, book_years, obs_years)
    _parse_sort_and_type(st)
    _parse_persons(st, R.load_persons(conn))
    _parse_taxa(st, conn)
    _parse_phenophase(st, conn)
    _parse_place(st, conn)
    left = _meaningful_leftovers(st)
    if left and use_llm:
        ctx = {"book_years": sorted(book_years), "unparsed_words": left,
               "already_parsed": {k: v for k, v in st.filters.items()}}
        data = _llm_call(text_, ctx)
        if data:
            _merge_llm(st, conn, data)
            left = []
    if left:
        st.warnings.append("Не распознано: " + ", ".join(left))
    st.labels.pop("_taxon_group", None)
    try:
        flt = SearchFilters.model_validate(st.filters)
    except Exception as e:  # noqa: BLE001 — LLM вернул что-то невалидное: оставляем только валидные поля
        clean = {}
        for k, v in st.filters.items():
            try:
                SearchFilters.model_validate({k: v})
                clean[k] = v
            except Exception:  # noqa: BLE001
                st.warnings.append(f"Поле {k} отброшено: некорректное значение")
        flt = SearchFilters.model_validate(clean)
        log.info("query_parse: %s", e)
    return {"filters": flt.to_json(), "explanation": _explain(st, flt), "warnings": st.warnings}
