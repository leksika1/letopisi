"""ВРЕМЕННАЯ замена pipeline/ingest: дампер PDF с текстовым слоем (книга 2020) в chunks по контракту.

Когда загрузчик ``pipeline/ingest`` (задача Б-2) будет готов, этот модуль не нужен: ядро читает
``data/chunks/<document_id>.jsonl`` от кого угодно. Здесь — минимально достаточная реализация,
чтобы ядро можно было разрабатывать и проверять уже сейчас.

Что делает:
- текст страницы берётся блоками PyMuPDF (``get_text("dict")``); блок = абзац (kind paragraph,
  caption для «Рисунок …/Таблица …», list для «• …»); соседние блоки одного абзаца склеиваются;
- номер страницы из колонтитула (отдельная строка-число вверху/внизу) → ``locator.printed_page``;
- таблицы — ``page.find_tables()``; строка таблицы = chunk ``kind=table_row`` с текстом
  «Заголовок: значение; …»; строка-группа (одна непустая ячейка, например «Болото верховое
  сфагновое») — только текст ячейки; шапка таблицы на следующей странице наследуется (таблица
  продолжается без повторной шапки);
- контракт Chunk не содержит координат, поэтому координаты строк пишутся в отдельный файл
  ``data/chunks/<document_id>.lines.jsonl``: ``{"chunk_id", "pdf_page", "bbox", "lines": [[start, end, x0, y0, x1, y1], …]}``
  (доли страницы, начало — левый верхний угол). Их использует ``pipeline.anchor``.

Запуск: ``python -m pipeline.devtools.quick_chunks data/raw/book27_2020.pdf --document-id 3 [--pages 69-79]``
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.normalize.persons import is_author_line

ROOT = Path(__file__).resolve().parents[2]

_DATAISH = re.compile(r"\d{1,2}\.\d{2}|^[\d\s.,/()+\-—–%°хx<>~]*\d[\d\s.,/()+\-—–%°хx<>~]*$")
_YEAR_HEADER = re.compile(r"^(19|20)\d{2}(\s*г\.?)?$")
_NUMBERING = re.compile(r"^\d+\.?$")


def _cell(v) -> str:
    if v is None:
        return ""
    s = str(v).replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("; ", ", ")


def _dataish(c: str) -> bool:
    return bool(c) and not _YEAR_HEADER.match(c) and bool(_DATAISH.search(c))


def _nonempty(row: list[str]) -> list[str]:
    return [c for c in row if c]


def _header_like(row: list[str]) -> bool:
    ne = _nonempty(row)
    if len(ne) < 2:
        return False
    if _NUMBERING.match(row[0] or "") and len(row[0]) <= 4 and row[0].endswith("."):
        return False
    return not any(_dataish(c) for c in row[1:] if c) or all(re.fullmatch(r"\d{1,2}[аб]?|(19|20)\d{2}", c) for c in ne[1:])


def _numbering_row(row: list[str]) -> bool:
    ne = _nonempty(row)
    return len(ne) >= 2 and all(c.isdigit() for c in ne) and [int(c) for c in ne] == list(range(int(ne[0]), int(ne[0]) + len(ne)))


def _data_row(row: list[str]) -> bool:
    ne = _nonempty(row)
    return len(ne) >= 2 and any(_dataish(c) for c in row[1:] if c)


def _clean_key(k: str) -> str:
    k = re.sub(r"(\w)- (\w)", r"\1\2", k)  # «наступле- ния» → «наступления»
    k = re.sub(r"\s+", " ", k).strip()
    if not re.fullmatch(r"\d{1,2}[аб]?", k):
        k2 = re.sub(r"(?<!\S)\d{1,2}(?!\S)", " ", k)  # номера столбцов, слитые с шапкой («№ 1»)
        k2 = re.sub(r"\s+", " ", k2).strip()
        k = k2 or k
    return k.replace(": ", " ").rstrip(":")


def _make_keys(cols: list[str]) -> list[str]:
    keys, seen = [], set()
    for i, c in enumerate(cols):
        k = _clean_key(c) if c else f"колонка {i + 1}"
        if k in seen:
            k = f"{k} ({i + 1})"
        seen.add(k)
        keys.append(k)
    return keys


@dataclass
class TableState:
    keys: list[str]
    ncols: int
    x0: float
    x1: float
    page: int


@dataclass
class Item:
    y0: float
    x0: float
    kind: str
    text: str
    lines: list[list[float]] = field(default_factory=list)  # [start, end, x0, y0, x1, y1]
    bbox: list[float] | None = None


def _frac(b, W, H) -> list[float]:
    return [round(max(0.0, b[0] / W), 4), round(max(0.0, b[1] / H), 4),
            round(min(1.0, b[2] / W), 4), round(min(1.0, b[3] / H), 4)]


def _union(bs):
    bs = [b for b in bs if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def _block_items(page, pno, W, H, table_boxes) -> tuple[list[Item], str | None]:
    d = page.get_text("dict")
    items: list[Item] = []
    printed = None
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        bx = b["bbox"]
        cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        if any(t[0] - 2 <= cx <= t[2] + 2 and t[1] - 2 <= cy <= t[3] + 2 for t in table_boxes):
            continue
        text_parts, lines = [], []
        pos = 0
        for ln in b["lines"]:
            s = "".join(sp["text"] for sp in ln["spans"])
            if not s.strip():
                continue
            if text_parts:
                text_parts.append("\n")
                pos += 1
            text_parts.append(s)
            lines.append([pos, pos + len(s), *_frac(ln["bbox"], W, H)])
            pos += len(s)
        text = "".join(text_parts)
        if not text.strip():
            continue
        st = text.strip()
        if re.fullmatch(r"\d{1,4}", st) and (bx[1] < 0.1 * H or bx[3] > 0.9 * H) and abs(int(st) - pno) <= 40:
            printed = printed or st
            continue
        # строку авторов под заголовком раздела отделяем от следующего за ней абзаца
        first_line = text[: lines[0][1]] if lines else text
        if len(lines) > 1 and is_author_line(first_line):
            cut = lines[1][0]
            items.append(Item(lines[0][3] * H, lines[0][2] * W, "paragraph", first_line, [lines[0]], lines[0][2:]))
            rest = text[cut:]
            rest_lines = [[a - cut, b - cut, *bb] for a, b, *bb in lines[1:]]
            items.append(Item(rest_lines[0][3] * H, bx[0], _kind(rest), rest, rest_lines, _union([ln[2:] for ln in rest_lines])))
            continue
        items.append(Item(bx[1], bx[0], _kind(text), text, lines, _frac(bx, W, H)))
    return items, printed


def _kind(text: str) -> str:
    if re.match(r"^\s*(Рисун|Рис\.|Таблица\s*\d|Табл\.)", text):
        return "caption"
    if re.match(r"^\s*[•▪●]", text):
        return "list"
    return "paragraph"


def _merge_paragraphs(items: list[Item]) -> list[Item]:
    out: list[Item] = []
    for it in items:
        if out and it.kind == "paragraph" and out[-1].kind in ("paragraph", "list", "caption"):
            prev = out[-1]
            gap = (it.bbox[1] if it.bbox else 0) - (prev.bbox[3] if prev.bbox else 0)
            cont_text = (prev.kind != "caption" and not re.search(r"[.:;!?»)]\s*$", prev.text)
                         and re.match(r"^\s*[a-zа-яё(«]", it.text))
            # хвост подписи рисунка («Фото: Валова» + «Е.В.»)
            cont_caption = (prev.kind == "caption" and not prev.text.rstrip().endswith(".")
                            and len(it.text.strip()) < 60 and "\n" not in it.text.strip())
            if (cont_text or cont_caption) and gap < 0.03:
                off = len(prev.text) + 1
                prev.text = prev.text + "\n" + it.text
                prev.lines += [[a + off, b + off, *bb] for a, b, *bb in it.lines]
                prev.bbox = _union([prev.bbox, it.bbox])
                continue
        out.append(it)
    return out


def _table_items(page, pno, W, H, prev: TableState | None) -> tuple[list[Item], list[list[float]], TableState | None]:
    items: list[Item] = []
    boxes: list[list[float]] = []
    try:
        tabs = page.find_tables().tables
    except Exception:  # noqa: BLE001 — find_tables иногда падает на сложной вёрстке
        return [], [], prev
    first = True
    for t in tabs:
        raw = t.extract()
        rows = [[_cell(c) for c in r] for r in raw]
        if sum(_data_row(r) for r in rows) < 2:
            continue  # это не таблица данных (например, текст, ошибочно найденный как таблица)
        ncols = len(rows[0]) if rows else 0
        tb = t.bbox
        boxes.append(list(tb))
        cont = (
            first and prev is not None and prev.page == pno - 1 and prev.ncols == ncols
            and tb[1] < 0.15 * H and abs(tb[0] - prev.x0) < 15 and abs(tb[2] - prev.x1) < 15
            and not _header_like(rows[0])
        )
        first = False
        if cont:
            keys, data = prev.keys, rows
        else:
            hdr_names = [_cell(n) for n in (t.header.names or [])]
            if t.header.external and len(_nonempty(hdr_names)) >= 2 and all(len(n) < 80 for n in hdr_names):
                keys, data = _make_keys(hdr_names), rows
            elif rows and _header_like(rows[0]):
                hdr = [list(rows[0])]
                i = 1
                while i < len(rows) and i < 6 and not _data_row(rows[i]) and not _numbering_row(rows[i]) \
                        and len(_nonempty(rows[i])) >= 2:
                    hdr.append(rows[i])
                    i += 1
                while i < len(rows) and _numbering_row(rows[i]):
                    i += 1
                cols = [" ".join(h[c] for h in hdr if c < len(h) and h[c]) for c in range(ncols)]
                keys, data = _make_keys(cols), rows[i:]
            else:
                keys, data = _make_keys([""] * ncols), rows
        rects = t.rows
        offset = len(rows) - len(data)
        for ri, r in enumerate(data):
            ne = [(ci, c) for ci, c in enumerate(r) if c]
            if not ne:
                continue
            rrect = rects[ri + offset] if ri + offset < len(rects) else None
            rb = list(rrect.bbox) if rrect is not None else list(tb)
            cells = list(rrect.cells) if rrect is not None else [None] * ncols
            if len(ne) == 1:
                text = ne[0][1]
                lines = [[0, len(text), *_frac(rb, W, H)]]
            else:
                parts, lines, pos = [], [], 0
                for ci, c in ne:
                    if parts:
                        parts.append("; ")
                        pos += 2
                    k = keys[ci] if ci < len(keys) else f"колонка {ci + 1}"
                    seg = f"{k}: {c}"
                    parts.append(seg)
                    cb = cells[ci] if ci < len(cells) and cells[ci] else rb
                    lines.append([pos + len(k) + 2, pos + len(seg), *_frac(cb, W, H)])
                    pos += len(seg)
                text = "".join(parts)
            items.append(Item(rb[1], rb[0], "table_row", text, lines, _frac(rb, W, H)))
        prev = TableState(keys, ncols, tb[0], tb[2], pno)
    return items, boxes, prev


def dump(pdf_path: str | Path, document_id: int, pages: tuple[int, int] | None = None,
         verbose: bool = False) -> tuple[list[dict], list[dict]]:
    """→ (chunks по контракту, координаты строк для pipeline.anchor)."""
    import fitz

    doc = fitz.open(str(pdf_path))
    a, b = pages or (1, doc.page_count)
    chunks, lines_out = [], []
    prev: TableState | None = None
    ord_ = 0
    t0 = time.time()
    for pno in range(a, min(b, doc.page_count) + 1):
        page = doc[pno - 1]
        W, H = page.rect.width, page.rect.height
        t_items, t_boxes, prev = _table_items(page, pno, W, H, prev)
        b_items, printed = _block_items(page, pno, W, H, t_boxes)
        items = _merge_paragraphs(sorted(b_items, key=lambda i: (round(i.y0), i.x0)))
        items = sorted(items + t_items, key=lambda i: (round(i.y0), i.x0))
        for it in items:
            cid = document_id * 1_000_000 + ord_ + 1
            chunks.append({
                "id": cid, "document_id": document_id, "section_id": None, "ord": ord_, "kind": it.kind,
                "locator": {"pdf_page": pno, "half": None, "printed_page": printed},
                "text": it.text, "text_source": "text_layer", "ocr_confidence": None,
            })
            lines_out.append({"chunk_id": cid, "pdf_page": pno, "bbox": it.bbox, "lines": it.lines})
            ord_ += 1
        if verbose and pno % 50 == 0:
            print(f"  стр. {pno}: {len(chunks)} chunks, {time.time() - t0:.0f} с", file=sys.stderr)
    return chunks, lines_out


def write(chunks: list[dict], lines: list[dict], out_dir: Path, document_id: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{document_id}.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with (out_dir / f"{document_id}.lines.jsonl").open("w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("--document-id", type=int, required=True)
    ap.add_argument("--pages", help="диапазон страниц PDF, например 69-79")
    ap.add_argument("--out", default=str(ROOT / "data" / "chunks"))
    a = ap.parse_args(argv)
    pages = tuple(int(x) for x in a.pages.split("-")) if a.pages else None
    chunks, lines = dump(a.pdf, a.document_id, pages, verbose=True)
    p = write(chunks, lines, Path(a.out), a.document_id)
    kinds: dict[str, int] = {}
    for c in chunks:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"{p}: {len(chunks)} chunks {kinds}")


if __name__ == "__main__":
    main()
