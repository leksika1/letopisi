"""Дерево разделов документа и привязка каждого chunk к разделу.

Два режима:

1. **Есть оглавление** (книга 2020, стр. 3–4): строки «7.3 Сезонная динамика растительных
   сообществ … 69» разбираются в записи (код, название, печатная страница). Для каждой записи
   ищется chunk-заголовок на её странице (нормализованное начало строки / нечёткое совпадение),
   и раздел начинается ровно с этого chunk — поэтому страница, где кончается 7.3 и начинается 7.4,
   делится правильно. Дополнительно ищутся подзаголовки внутри разделов, за которыми идёт строка
   авторов («1. ОСОБЕННОСТИ ЭКОЛОГИИ МЕЛКИХ МЛЕКОПИТАЮЩИХ» + «Крюкова С.А.»), — они становятся
   дочерними разделами (code = null), чтобы правильно подставлять наблюдателя.

2. **Оглавления нет** (сканы): заголовки ищутся по шаблонам «N.N Название», «N. ЗАГЛАВНЫЕ»,
   строка целиком заглавными буквами, короткая строка без точки в начале фрагмента, а также любая
   короткая строка, за которой следует строка авторов. Многострочный заголовок склеивается
   («Млекопитающие / Результаты зимнего маршрутного учета… / (март 1995 года)»).

Авторы раздела — строка фамилий с инициалами сразу под заголовком (``persons.is_author_line``):
``authors_raw`` + ``person_ids``. В контракте Section нет поля авторов, поэтому они пишутся
отдельным файлом ``data/sections/<document_id>.authors.json`` (см. отчёт: предложение в контракт).

Идентификаторы разделов: ``document_id * 10000 + порядковый номер`` (уникальны между документами).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from pipeline.normalize.persons import is_author_line, match_people
from pipeline.normalize.text import fold

_TOC_TITLE = re.compile(r"^\s*(СОДЕРЖАНИЕ|ОГЛАВЛЕНИЕ)\s*$", re.M)
_NUM_ONLY = re.compile(r"^\s*(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?\s*$")
_TITLE_NUM = re.compile(r"^(.*\S)\s+(\d{1,4})(?:\s*[-–]\s*\d{1,4})?\s*$")
_CODE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})*)\.?\s+(.+)$")
_H_NUMBERED = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})+)\.?\s+([А-ЯЁA-Z].{2,150})$")
_H_UPPER = re.compile(r"^\s*(?:(\d{1,2})\.\s+)?([А-ЯЁ][А-ЯЁ\s,«»\"()\-–—]{4,150})$")


@dataclass
class TocEntry:
    code: str | None
    title: str
    printed_page: int | None


@dataclass
class SectionsResult:
    sections: list[dict] = field(default_factory=list)  # по контракту Section
    authors: dict[int, dict] = field(default_factory=dict)  # section_id → {authors_raw, person_ids}
    chunk_section: dict[int, int | None] = field(default_factory=dict)  # chunk_id → section_id
    toc_pages: set[int] = field(default_factory=set)
    mode: str = "toc"

    def section(self, sid: int | None) -> dict | None:
        return next((s for s in self.sections if s["id"] == sid), None) if sid else None

    def authors_for(self, sid: int | None) -> dict | None:
        """Авторы раздела или ближайшего предка, у которого они указаны."""
        seen = 0
        while sid and seen < 10:
            a = self.authors.get(sid)
            if a and a.get("authors_raw"):
                return {**a, "section_id": sid}
            s = self.section(sid)
            sid = s["parent_id"] if s else None
            seen += 1
        return None


def _page(c: dict) -> int | None:
    return (c.get("locator") or {}).get("pdf_page")


def _norm(s: str) -> str:
    s = fold(s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_line(text: str) -> str:
    for ln in text.split("\n"):
        if ln.strip():
            return ln.strip()
    return ""


# --------------------------------------------------------------------------- оглавление

def find_toc_pages(chunks: list[dict], max_page: int = 12) -> list[int]:
    by_page: dict[int, list[dict]] = {}
    for c in chunks:
        p = _page(c)
        if p and p <= max_page + 3:
            by_page.setdefault(p, []).append(c)
    start = next((p for p in sorted(by_page) if p <= max_page and any(_TOC_TITLE.search(c["text"]) for c in by_page[p])), None)
    if start is None:
        return []
    pages = [start]
    p = start + 1
    while p in by_page:
        lines = [ln.strip() for c in by_page[p] for ln in c["text"].split("\n") if ln.strip()]
        numeric = sum(bool(_NUM_ONLY.match(ln) or _TITLE_NUM.match(ln)) for ln in lines)
        if lines and numeric / len(lines) >= 0.35 and not any(len(ln) > 160 for ln in lines):
            pages.append(p)
            p += 1
        else:
            break
    return pages


def parse_toc(text: str) -> list[TocEntry]:
    """Записи оглавления. Номера страниц могут стоять в отдельных строках и даже раньше
    переноса названия («2. Пробные … постоянные» / «9» / «(временные) маршруты»), поэтому
    строка-продолжение (со строчной буквы, «(», «»») присоединяется к текущей записи, а номер —
    к последней записи без номера. Запись без номера получает номер следующей записи."""
    items: list[list] = []  # [title, page]
    for raw in text.split("\n"):
        s = raw.strip()
        if not s or _TOC_TITLE.match(s):
            continue
        m = _NUM_ONLY.match(s)
        if m:
            pending = next((it for it in reversed(items) if it[1] is None), None)
            if pending is not None:
                pending[1] = int(m.group(1))
            continue
        page = None
        m = _TITLE_NUM.match(s)
        if m and re.search(r"[А-Яа-яЁёA-Za-z]{3}", m.group(1)) and not re.match(r"^\d+(\.\d+)*\.?$", m.group(1)):
            s, page = m.group(1), int(m.group(2))
        if items and re.match(r"^[a-zа-яё(«\"]", s):
            items[-1][0] += " " + s
            if page is not None and items[-1][1] is None:
                items[-1][1] = page
            continue
        items.append([s, page])
    for i, it in enumerate(items):
        if it[1] is None:
            it[1] = next((x[1] for x in items[i + 1:] if x[1] is not None), None)
    return [e for e in (_entry(t, p) for t, p in items) if e.title]


def _entry(title: str, page: int | None) -> TocEntry:
    title = re.sub(r"\s+", " ", title).strip(" .")
    m = _CODE.match(title)
    if m:
        return TocEntry(m.group(1), m.group(2).strip(" ."), page)
    return TocEntry(None, title, page)


# --------------------------------------------------------------------------- построение

def build_sections(document_id: int, chunks: list[dict], last_page: int | None = None) -> SectionsResult:
    chunks = sorted(chunks, key=lambda c: c["ord"])
    if not chunks:
        return SectionsResult()
    last_page = last_page or max((_page(c) or 0) for c in chunks) or None
    toc_pages = find_toc_pages(chunks)
    if toc_pages:
        toc_text = "\n".join(c["text"] for c in chunks if _page(c) in toc_pages)
        entries = parse_toc(toc_text)
        if len(entries) >= 3:
            res = _from_toc(document_id, chunks, entries, set(toc_pages), last_page)
            res.toc_pages = set(toc_pages)
            return res
    res = _generic(document_id, chunks, last_page)
    res.mode = "headings"
    return res


def _printed_to_pdf(chunks: list[dict]) -> dict[int, int]:
    m: dict[int, int] = {}
    for c in chunks:
        loc = c.get("locator") or {}
        pp, p = loc.get("printed_page"), loc.get("pdf_page")
        if pp and p and re.fullmatch(r"\d+", str(pp)):
            m.setdefault(int(pp), p)
    return m


def _heading_match(entry: TocEntry, text: str) -> bool:
    line = _norm(" ".join(text.split("\n")[:3]))
    want = _norm(f"{entry.code or ''} {entry.title}")
    if not want:
        return False
    n = min(len(want), 45)
    if line.startswith(want[:n]):
        return True
    if entry.code and not re.match(rf"^{re.escape(entry.code.replace('.', ' '))}\b", line):
        return False
    return fuzz.partial_ratio(want[:60], line[: len(want) + 20]) >= 88 and len(line) < len(want) + 80


def _from_toc(document_id, chunks, entries, toc_pages, last_page) -> SectionsResult:
    p2p = _printed_to_pdf(chunks)
    res = SectionsResult(mode="toc")
    by_page: dict[int, list[dict]] = {}
    for c in chunks:
        by_page.setdefault(_page(c) or 0, []).append(c)
    starts: list[tuple[int, dict]] = []  # (ord chunk-заголовка, section)
    code_to_id: dict[str, int] = {}
    n = 0
    for e in entries:
        n += 1
        sid = document_id * 10000 + n
        pdf_from = p2p.get(e.printed_page, e.printed_page) if e.printed_page else None
        head = None
        last_ord = max((o for o, _ in starts), default=-1)
        if pdf_from:
            for p in (pdf_from, pdf_from + 1, pdf_from - 1, pdf_from + 2, pdf_from - 2, pdf_from + 3, pdf_from - 3):
                if p in toc_pages:
                    continue
                for c in by_page.get(p, []):
                    if c["kind"] != "table_row" and c["ord"] > last_ord and _heading_match(e, c["text"]):
                        head = c
                        break
                if head:
                    break
        if head is None and pdf_from and pdf_from not in toc_pages:
            head = next((c for c in by_page.get(pdf_from, []) if c["ord"] > last_ord), None)
        parent = None
        if e.code and "." in e.code:
            parent = code_to_id.get(e.code.rsplit(".", 1)[0])
        sec = {"id": sid, "document_id": document_id, "parent_id": parent, "code": e.code, "title": e.title,
               "pdf_page_from": _page(head) if head else pdf_from, "pdf_page_to": None}
        if e.code:
            code_to_id[e.code] = sid
        res.sections.append(sec)
        if head is not None:
            starts.append((head["ord"], sec))
            res.authors[sid] = _authors_after(chunks, head)
    # подзаголовки с авторами внутри разделов (не из оглавления)
    head_ords = {o for o, _ in starts}
    toc_starts = list(starts)
    for i, c in enumerate(chunks):
        if c["ord"] in head_ords or _page(c) in toc_pages or c["kind"] == "table_row":
            continue
        title = _subheading(c["text"])
        if not title:
            continue
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        if not nxt or not is_author_line(_first_line(nxt["text"])):
            continue
        if i > 0 and chunks[i - 1]["ord"] in head_ords:
            continue  # продолжение заголовка раздела из оглавления («12. … ЗАПОВЕДНИКА» + «И ОКРЕСТНОСТЕЙ»)
        k = i - 1
        while k >= 0 and i - k <= 3 and chunks[k]["ord"] not in head_ords and chunks[k]["kind"] != "table_row" \
                and _H_UPPER.match(" ".join(chunks[k]["text"].split())):
            title = " ".join(chunks[k]["text"].split()) + " " + title
            k -= 1
        owner = _owner(toc_starts, c["ord"])
        n += 1
        sid = document_id * 10000 + n
        sec = {"id": sid, "document_id": document_id, "parent_id": owner["id"] if owner else None, "code": None,
               "title": title, "pdf_page_from": _page(c), "pdf_page_to": None}
        res.sections.append(sec)
        starts.append((chunks[k + 1]["ord"], sec))
        res.authors[sid] = _authors_after(chunks, c)
    _finish(res, chunks, starts, last_page, toc_pages)
    return res


def _subheading(text: str) -> str | None:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines or len(lines) > 3:
        return None
    t = " ".join(lines)
    if len(t) > 160 or t.endswith((".", ",", ";", ":")) and not t.endswith("..."):
        return None
    if _H_NUMBERED.match(t) or _H_UPPER.match(t):
        return re.sub(r"\s+", " ", t).strip()
    if len(t) < 100 and re.match(r"^[А-ЯЁ0-9]", t) and not re.search(r"\d{1,2}\.\d{2}", t):
        return t
    return None


def _owner(starts, ord_) -> dict | None:
    best = None
    for o, s in sorted(starts, key=lambda x: x[0]):
        if o <= ord_:
            best = s
    # подзаголовок вкладывается в самый глубокий раздел оглавления
    return best


def _authors_after(chunks: list[dict], head: dict) -> dict:
    """Строка авторов: вторая строка заголовка или первые строки 1–2 следующих chunk."""
    cands: list[str] = []
    lines = [ln.strip() for ln in head["text"].split("\n") if ln.strip()]
    cands += lines[1:4]
    idx = next((i for i, c in enumerate(chunks) if c["id"] == head["id"]), None)
    if idx is not None:
        for c in chunks[idx + 1: idx + 3]:
            if c["kind"] == "table_row":
                break
            cands += [ln.strip() for ln in c["text"].split("\n") if ln.strip()][:2]
    for ln in cands[:6]:
        if is_author_line(ln):
            raw = ln.strip()
            ids, _ = match_people(raw)
            return {"authors_raw": raw, "person_ids": ids}
    return {"authors_raw": None, "person_ids": []}


def _depth(sec: dict) -> int:
    return sec["code"].count(".") + 1 if sec.get("code") else 1


def _finish(res: SectionsResult, chunks, starts, last_page, toc_pages):
    starts.sort(key=lambda x: x[0])
    by_id = {s["id"]: s for s in res.sections}

    def depth(s):  # подзаголовки без кода — глубже родителя
        d, cur, k = 0, s, 0
        while cur and k < 10:
            d += 1
            cur = by_id.get(cur["parent_id"]) if cur["parent_id"] else None
            k += 1
        return d

    ords = [c["ord"] for c in chunks]
    page_of_ord = {c["ord"]: _page(c) for c in chunks}
    first_ord_on_page: dict[int, int] = {}
    for c in chunks:
        first_ord_on_page.setdefault(_page(c) or 0, c["ord"])
    for i, (o, s) in enumerate(starts):
        end_page = last_page
        for o2, s2 in starts[i + 1:]:
            if depth(s2) <= depth(s):
                p2 = page_of_ord.get(o2)
                if p2:
                    end_page = p2 - 1 if first_ord_on_page.get(p2) == o2 else p2
                break
        s["pdf_page_from"] = s["pdf_page_from"] or page_of_ord.get(o)
        if end_page and s["pdf_page_from"]:
            s["pdf_page_to"] = max(end_page, s["pdf_page_from"])
    # разделы без найденного заголовка: диапазон по оглавлению
    for s in res.sections:
        if s["pdf_page_to"] is None and s["pdf_page_from"]:
            later = [x["pdf_page_from"] for x in res.sections if x["pdf_page_from"] and x["pdf_page_from"] > s["pdf_page_from"]]
            s["pdf_page_to"] = (min(later) - 1) if later else last_page
    # привязка chunk → раздел: каждый chunk принадлежит последнему начавшемуся разделу
    j, cur = 0, None
    for c in sorted(chunks, key=lambda c: c["ord"]):
        while j < len(starts) and starts[j][0] <= c["ord"]:
            cur = starts[j][1]
            j += 1
        if _page(c) in toc_pages:
            res.chunk_section[c["id"]] = None
        else:
            res.chunk_section[c["id"]] = cur["id"] if cur else None
    del ords


# --------------------------------------------------------------------------- без оглавления

def _heading_line(ln: str) -> bool:
    s = ln.strip()
    if not s or len(s) > 110 or is_author_line(s):
        return False
    if s.endswith((".", ",", ";", ":")) and not re.fullmatch(r"\d+\.\s+.*", s):
        return False
    if re.search(r"\d{1,2}\.\d{2}", s):
        return False
    return bool(re.match(r"^[А-ЯЁA-Z(«]", s)) and len(s.split()) <= 12


def _generic(document_id: int, chunks: list[dict], last_page) -> SectionsResult:
    res = SectionsResult(mode="headings")
    starts: list[tuple[int, dict]] = []
    code_to_id: dict[str, int] = {}
    n = 0
    for i, c in enumerate(chunks):
        if c["kind"] == "table_row":
            continue
        lines = [ln.strip() for ln in c["text"].split("\n")]
        k = 0
        while k < len(lines) and not lines[k]:
            k += 1
        found = None
        for li in range(k, min(len(lines), k + 40)):
            s = lines[li]
            if not s:
                continue
            prev_ok = li == k or not lines[li - 1] or lines[li - 1].endswith((".", ":", ")")) or _heading_line(lines[li - 1]) is False and lines[li - 1].endswith(".")
            m_num = _H_NUMBERED.match(s)
            m_up = _H_UPPER.match(s) if len(s.split()) >= 1 and s.upper() == s and re.search(r"[А-ЯЁ]{4}", s) else None
            nxt = [x for x in lines[li + 1: li + 4] if x]
            next_author = bool(nxt) and is_author_line(nxt[0].rstrip(".") if nxt[0].endswith(".") else nxt[0])
            at_start = li == k and _heading_line(s) and len(s.split()) <= 5
            if (m_num or m_up) and prev_ok or (_heading_line(s) and next_author and prev_ok) or at_start:
                found = li
                break
        if found is None:
            continue
        # многострочный заголовок
        title_lines = [lines[found]]
        for s in lines[found + 1: found + 4]:
            if not s or is_author_line(s.rstrip(".")):
                break
            if _heading_line(s) and not re.search(r"(?<!\()\b\d{4}\b(?![^(]*\))", s):
                title_lines.append(s)
            elif re.fullmatch(r"\([^)]{3,60}\)", s):
                title_lines.append(s)
            else:
                break
        title = re.sub(r"\s+", " ", " ".join(title_lines)).strip()
        if len(title_lines) > 1 and not title_lines[0].endswith((".", ":")) and not title_lines[1].startswith("("):
            title = title_lines[0].rstrip(".") + ". " + " ".join(title_lines[1:])
        m = _CODE.match(title)
        code = m.group(1) if m and _H_NUMBERED.match(title) or (m and _H_UPPER.match(title)) else None
        if code:
            title = m.group(2)
        n += 1
        sid = document_id * 10000 + n
        parent = code_to_id.get(code.rsplit(".", 1)[0]) if code and "." in code else None
        sec = {"id": sid, "document_id": document_id, "parent_id": parent, "code": code, "title": title.strip(" ."),
               "pdf_page_from": _page(c), "pdf_page_to": None}
        if code:
            code_to_id[code] = sid
        res.sections.append(sec)
        starts.append((c["ord"], sec))
        # авторы: строка фамилий с инициалами в 1–8 строках после заголовка
        tail = [x for x in lines[found + len(title_lines): found + len(title_lines) + 8] if x]
        if i + 1 < len(chunks):
            tail += [x.strip() for x in chunks[i + 1]["text"].split("\n")[:2] if x.strip()]
        auth = {"authors_raw": None, "person_ids": []}
        for ln in tail:
            cand = ln.rstrip(".") if ln.endswith(".") and not re.search(r"[А-ЯЁ]\.$", ln[:-1] + ".") else ln
            if is_author_line(ln) or is_author_line(cand) or is_author_line(ln.rstrip(".")):
                ids, _ = match_people(ln)
                auth = {"authors_raw": ln.strip(" ."), "person_ids": ids}
                break
        res.authors[sid] = auth
    _finish(res, chunks, starts, last_page, set())
    return res
