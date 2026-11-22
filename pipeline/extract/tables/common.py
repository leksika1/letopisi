"""Общее для детерминированных парсеров: разбор строки таблицы и черновик записи."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# «Заголовок: значение; Заголовок: значение» — формат chunk kind=table_row (контракт ingest)
_SPLIT = re.compile(r";\s(?=[^;]{1,200}?:\s)")


@dataclass
class Cell:
    key: str
    value: str
    start: int  # начало «key: value» в chunk.text
    vstart: int  # начало значения
    end: int


def parse_row(text: str) -> list[Cell] | None:
    """Ячейки строки таблицы с позициями в тексте. None — строка-группа (без «ключ: значение»)."""
    if ": " not in text:
        return None
    cells: list[Cell] = []
    pos = 0
    parts = _SPLIT.split(text)
    for part in parts:
        start = text.index(part, pos)
        k, sep, v = part.partition(": ")
        if not sep:
            # кусок без двоеточия — продолжение предыдущего значения
            if cells:
                cells[-1].value += "; " + part
                cells[-1].end = start + len(part)
            pos = start + len(part)
            continue
        cells.append(Cell(k.strip(), v.strip(), start, start + len(k) + 2, start + len(part)))
        pos = start + len(part)
    if len(cells) < 2:
        return None
    return cells


def find_key(cells: list[Cell], *prefixes: str) -> Cell | None:
    for c in cells:
        lk = c.key.lower()
        if any(lk.startswith(p) for p in prefixes):
            return c
    return None


EMPTY_VALUES = {"", "-", "—", "–", "нет", "н/д", "x", "х", "?"}
DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
FIG_RE = re.compile(r"\s*Рис(?:унок|унки|\.)\s*\d", re.I)


@dataclass
class Draft:
    """Черновик записи от парсера. Поля — подмножество Observation; quote — подстрока chunk.text."""
    fields: dict
    quote: str
    quote_span: tuple[int, int] | None = None
    strategy: str = "table"
    uncertain: bool = False
    notes: list[str] = field(default_factory=list)
    printed_pages: str | None = None
    observers_from_section: bool = True  # можно ли подставить авторов раздела
    chunk_ref: int = 0  # индекс chunk в окне (для LLM)


def span_quote(text: str, a: int, b: int) -> tuple[str, tuple[int, int]]:
    """Подстрока [a, b) без краевых пробелов — гарантированно дословная."""
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return text[a:b], (a, b)


def strip_figure(value: str) -> tuple[str, str | None]:
    """«Ledum palustre L. Рисунок 7.17 - …» → («Ledum palustre L.», «Рисунок 7.17 - …»)."""
    m = FIG_RE.search(value)
    if not m or m.start() == 0:
        return value, None
    return value[: m.start()].strip(), value[m.start():].strip()


def caption_year(caption: str | None) -> int | None:
    """Год из заголовка таблицы «… в 2020 году» (если год один)."""
    if not caption:
        return None
    ys = set(YEAR_RE.findall(caption))
    return int(ys.pop()) if len(ys) == 1 else None


def table_label(caption: str | None) -> str | None:
    m = re.search(r"Таблица\s*([\d.]+\d)", caption or "")
    return f"табл. {m.group(1)}" if m else None


def caption_title(caption: str | None) -> str | None:
    if not caption:
        return None
    t = re.sub(r"^\s*Таблица\s*[\d.]+\s*", "", caption)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.split(r"\s№\s", t)[0]  # шапка таблицы, попавшая в блок заголовка
    t = re.sub(r"(гг?\.)\d\b", r"\1", t)  # сноска «2020 гг.1»
    return t[:200].strip() or None


@dataclass
class TableContext:
    """Состояние текущей таблицы, которое роутер переносит между строками (и страницами)."""
    caption: str | None = None  # «Таблица 7.10 Сравнительная хронология …»
    caption_page: int | None = None
    group: str | None = None  # последняя строка-группа («Болото верховое сфагновое», «Предзимье»)
    group_page: int | None = None
    kind: str | None = None  # тип таблицы, определённый по первой строке данных
    doc_year: int | None = None

    @property
    def label(self) -> str | None:
        return table_label(self.caption)
