"""Маршрутизация: chunk → стратегия извлечения.

Порядок решений для каждого chunk (в порядке документа, с переносом состояния таблицы):
1. Страницы оглавления, фрагменты без букв, разделы из ``SKIP_SECTIONS`` (введение, просвещение,
   историко-культурное наследие, международное сотрудничество, приложения) → ``skip``
   (для разделов — только LLM-стратегия; детерминированные парсеры работают везде).
2. «Таблица N …» → запоминается заголовок таблицы (+ следующий короткий абзац-продолжение), ``skip``.
3. Строка таблицы (kind=table_row):
   - строка-группа (одна ячейка): подпись «Рисунок …» → парсер подписей; иначе запоминается как
     местообитание/сезон для следующих строк;
   - тип таблицы известен (``tables.parsers.detect``) → детерминированный парсер;
   - неизвестная таблица → LLM (с заголовком таблицы и строкой-группой в контексте).
4. Подпись «Рисунок …» → парсер подписей; если не разобрана, но есть дата → LLM.
5. Остальной текст → LLM.

LLM-фрагменты собираются в окна (до ``WINDOW_CHARS`` символов и ``WINDOW_CHUNKS`` фрагментов
подряд в одном разделе и одной таблице) — один запрос на окно, модель указывает chunk_ref.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.normalize.dicts import load

from .captions import has_date, is_caption, parse_caption
from .tables.common import Draft, TableContext, parse_row, strip_figure
from .tables.parsers import detect, parse_calendar, parse_counts, parse_phenomatrix, parse_year_columns

WINDOW_CHARS = 2500
WINDOW_CHUNKS = 6
SKIP_SECTIONS = re.compile(
    r"^(введение|содержание|эколого-просвет|охрана историко|международное сотрудн|приложени)", re.I
)


@dataclass
class Routed:
    chunk: dict
    strategy: str  # skip / table:<тип> / caption / group / llm
    drafts: list[Draft] = field(default_factory=list)
    ctx: TableContext | None = None
    reason: str | None = None


@dataclass
class Window:
    chunks: list[dict]
    section_id: int | None
    prev_text: str | None
    ctx: TableContext | None


def _phase_names() -> dict[str, str]:
    return {p["code"]: p["name_ru"] for p in load().phenophases if p["applies_to"] == "plants" and p.get("name_ru")}


def _transposed(cells) -> bool:
    from pipeline.normalize.taxa import _LAT_BINOM_RE, match_taxon

    sp = next((c for c in cells if c.key.lower().startswith(("вид", "назв", "наим"))), None)
    if sp is None or _LAT_BINOM_RE.search(sp.value) or not match_taxon(sp.value).unmatched:
        return False
    return any(not match_taxon(c.key).unmatched for c in cells if c is not sp)


def _copy_ctx(ctx: TableContext) -> TableContext:
    return TableContext(ctx.caption, ctx.caption_page, ctx.group, ctx.group_page, ctx.kind, ctx.doc_year)


def route(chunks: list[dict], section_of: dict[int, int | None], section_title: dict[int, str],
          toc_pages: set[int], doc_year: int | None, skip_sections: bool = True) -> list[Routed]:
    out: list[Routed] = []
    ctx = TableContext(doc_year=doc_year)
    caption_open = False
    names = _phase_names()
    for c in sorted(chunks, key=lambda c: c["ord"]):
        text = c["text"]
        page = (c.get("locator") or {}).get("pdf_page")
        sid = section_of.get(c["id"], c.get("section_id"))
        title = section_title.get(sid, "") if sid else ""
        skip_llm = skip_sections and bool(SKIP_SECTIONS.match(title or ""))
        if page in toc_pages or not re.search(r"[А-Яа-яЁёA-Za-z]{2}", text):
            out.append(Routed(c, "skip", reason="оглавление/нет текста"))
            continue
        stripped = text.strip()
        if c["kind"] != "table_row" and re.match(r"^Таблица\s*\d", stripped):
            ctx = TableContext(caption=re.sub(r"\s+", " ", stripped), caption_page=page, doc_year=doc_year)
            caption_open = True
            out.append(Routed(c, "skip", reason="заголовок таблицы"))
            continue
        if c["kind"] != "table_row" and caption_open and len(stripped) < 250 and not is_caption(stripped):
            ctx.caption = (ctx.caption or "") + " " + re.sub(r"\s+", " ", stripped)
            out.append(Routed(c, "skip", reason="продолжение заголовка таблицы"))
            continue
        if c["kind"] == "table_row":
            caption_open = False
            cells = parse_row(text)
            if cells is None:
                if is_caption(stripped):
                    drafts, done = parse_caption(text)
                    if done:
                        out.append(Routed(c, "caption" if drafts else "skip", drafts, reason="подпись в таблице"))
                    else:
                        out.append(Routed(c, "llm" if has_date(text) and not skip_llm else "skip", ctx=_copy_ctx(ctx)))
                    continue
                ctx.group, ctx.group_page = re.sub(r"\s+", " ", stripped), page
                out.append(Routed(c, "group", reason="строка-группа"))
                continue
            kind = detect(cells)
            if kind == "counts" and _transposed(cells):
                kind = None  # «район × вид»: названия видов в шапке, а не в строках — отдаём LLM
            if kind:
                ctx.kind = kind
                if kind == "year_columns":
                    drafts = parse_year_columns(text, cells, ctx)
                    sp = next((x for x in cells if x.key.lower().startswith(("вид", "назв", "наим"))), None)
                    if sp:
                        _, fig = strip_figure(sp.value)
                        if fig:
                            off = text.find(fig, sp.vstart)
                            fd, _ = parse_caption(fig, offset=off)
                            drafts += fd
                elif kind == "calendar":
                    drafts = parse_calendar(text, cells, ctx)
                elif kind == "phenomatrix":
                    drafts = parse_phenomatrix(text, cells, ctx, names)
                    if ctx.caption_page and page and ctx.caption_page != page:
                        for d in drafts:
                            d.printed_pages = f"{ctx.caption_page}-{page}"
                else:
                    drafts = parse_counts(text, cells, ctx)
                out.append(Routed(c, f"table:{kind}", drafts, _copy_ctx(ctx)))
                continue
            out.append(Routed(c, "skip" if skip_llm else "llm", ctx=_copy_ctx(ctx), reason="неизвестная таблица"))
            continue
        caption_open = False
        if is_caption(stripped):
            drafts, done = parse_caption(text)
            if done:
                out.append(Routed(c, "caption" if drafts else "skip", drafts, reason="подпись к рисунку"))
            else:
                out.append(Routed(c, "llm" if has_date(text) and not skip_llm else "skip"))
            continue
        out.append(Routed(c, "skip" if skip_llm else "llm", reason="раздел без наблюдений" if skip_llm else None))
    return out


def windows(routed: list[Routed], section_of: dict[int, int | None]) -> list[Window]:
    """Сгруппировать подряд идущие LLM-фрагменты в окна. prev_text — фрагмент перед окном."""
    out: list[Window] = []
    cur: list[Routed] = []
    cur_prev: str | None = None
    last_text: str | None = None
    for r in routed:
        c = r.chunk
        if r.strategy == "llm" and cur:
            first = cur[0]
            size = sum(len(x.chunk["text"]) for x in cur) + len(c["text"])
            same_sec = section_of.get(first.chunk["id"]) == section_of.get(c["id"])
            same_kind = (first.chunk["kind"] == "table_row") == (c["kind"] == "table_row")
            same_tab = (first.ctx.caption if first.ctx else None) == (r.ctx.caption if r.ctx else None)
            if size > WINDOW_CHARS or len(cur) >= WINDOW_CHUNKS or not (same_sec and same_kind and same_tab):
                out.append(_mk(cur, cur_prev, section_of))
                cur = []
        if r.strategy == "llm":
            if not cur:
                cur_prev = last_text
            cur.append(r)
        elif cur:
            out.append(_mk(cur, cur_prev, section_of))
            cur = []
        last_text = c["text"]
    if cur:
        out.append(_mk(cur, cur_prev, section_of))
    return out


def _mk(cur: list[Routed], prev: str | None, section_of) -> Window:
    sid = section_of.get(cur[0].chunk["id"], cur[0].chunk.get("section_id"))
    ctx = cur[0].ctx if cur[0].chunk["kind"] == "table_row" else None
    if prev and len(prev) > 500:
        prev = "…" + prev[-500:]
    return Window([r.chunk for r in cur], sid, prev, ctx)
