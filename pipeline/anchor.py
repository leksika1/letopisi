"""Привязка цитаты к исходному тексту: quote → quote_span в chunk.text → bboxes на странице.

1. Точный поиск ``chunk.text.find(quote)``.
2. Поиск в нормализованном тексте: пробелы и переносы строк схлопнуты, перенос «сло-\\nво» склеен,
   ё→е, регистр, единые тире и кавычки. Найденный интервал переводится обратно в индексы
   исходного текста, а ``quote`` заменяется дословной подстрокой оригинала (контракт: quote —
   подстрока chunk.text).
3. Нечёткий поиск ``rapidfuzz.fuzz.partial_ratio_alignment`` по нормализованным строкам,
   порог 90. Цитата также заменяется дословной подстрокой.
4. Не нашлось → ``quote_not_found``, ``quote_span = None``, ``bboxes = []``.

bboxes — прямоугольники строк chunk, пересекающих интервал (координаты строк — из файла
``data/chunks/<id>.lines.jsonl``, если загрузчик их дал), иначе прямоугольник всего chunk,
иначе пусто (сканы без координат OCR).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 90.0
_DASHES = "‐‑‒–—―−"
_QUOTES = {"«": '"', "»": '"', "„": '"', "“": '"', "”": '"'}


@dataclass
class AnchorResult:
    quote: str | None
    span: tuple[int, int] | None
    bboxes: list[list[float]] = field(default_factory=list)
    method: str | None = None  # exact / normalized / fuzzy / None
    score: float = 0.0

    @property
    def found(self) -> bool:
        return self.span is not None


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Нормализованная строка и для каждого её символа — индекс в исходной строке."""
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        # перенос внутри слова: «буква-\n  буква» → склеить
        if ch == "-" and i + 1 < n and text[i + 1] in "\r\n" and out and out[-1].isalpha():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j].isalpha() and text[j].islower():
                i = j
                continue
        if ch.isspace() or ch == "­":
            if ch != "­" and out and out[-1] != " ":
                out.append(" ")
                idx.append(i)
            i += 1
            continue
        c = ch.lower()
        if c == "ё":
            c = "е"
        elif c in _DASHES:
            c = "-"
        else:
            c = _QUOTES.get(c, c)
        out.append(c)
        idx.append(i)
        i += 1
    # пробел перед знаками препинания и после «-» в «Заяц- беляк» не важен
    s = "".join(out)
    return s, idx


def _norm(s: str) -> str:
    return normalize_with_map(s)[0].strip()


def _back(idx: list[int], a: int, b: int, text: str) -> tuple[int, int]:
    """Интервал [a, b) нормализованной строки → интервал исходной."""
    a = max(0, min(a, len(idx) - 1))
    b = max(a + 1, min(b, len(idx)))
    start, end = idx[a], idx[b - 1] + 1
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def find_span(quote: str | None, text: str) -> tuple[tuple[int, int] | None, str | None, float]:
    if not quote or not text:
        return None, None, 0.0
    q = quote.strip()
    i = text.find(q)
    if i >= 0:
        return (i, i + len(q)), "exact", 100.0
    nt, idx = normalize_with_map(text)
    nq = _norm(q)
    if not nq or not nt:
        return None, None, 0.0
    j = nt.find(nq)
    if j >= 0:
        return _back(idx, j, j + len(nq), text), "normalized", 100.0
    # в нормализованном тексте ещё убираем пробелы вокруг дефиса («Заяц- беляк»)
    if len(nq) >= 8:
        al = fuzz.partial_ratio_alignment(nq, nt, score_cutoff=FUZZY_THRESHOLD)
        if al is not None and al.score >= FUZZY_THRESHOLD:
            return _back(idx, al.dest_start, al.dest_end, text), "fuzzy", float(al.score)
    return None, None, 0.0


def bboxes_for(span: tuple[int, int] | None, lines: list | None, chunk_bbox: list[float] | None) -> list[list[float]]:
    if span is None:
        return []
    a, b = span
    if lines:
        out = [list(ln[2:6]) for ln in lines if ln[0] < b and ln[1] > a]
        if out:
            return out
    return [list(chunk_bbox)] if chunk_bbox else []


def anchor(quote: str | None, text: str, lines: list | None = None, chunk_bbox: list[float] | None = None,
           known_span: tuple[int, int] | None = None) -> AnchorResult:
    """Найти цитату. known_span — интервал, уже известный парсеру (проверяется на совпадение)."""
    if known_span is not None:
        a, b = known_span
        if 0 <= a < b <= len(text) and (quote is None or text[a:b] == quote):
            return AnchorResult(text[a:b], (a, b), bboxes_for((a, b), lines, chunk_bbox), "exact", 100.0)
    span, method, score = find_span(quote, text)
    if span is None:
        return AnchorResult(quote, None, [], None, 0.0)
    return AnchorResult(text[span[0]:span[1]], span, bboxes_for(span, lines, chunk_bbox), method, score)
