import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image

from . import ocr
from .common import Box, Fragment, Stats, row_text


@dataclass
class TableHeader:
    labels: list[str]
    page: int
    left: float
    right: float

    def continues(self, page_no: int, rect: Box, first_row: list[str]) -> bool:
        if (
            page_no != self.page + 1
            or len(first_row) != len(self.labels)
            or rect[1] > 0.15
            or abs(rect[0] - self.left) > 0.02
            or abs(rect[2] - self.right) > 0.02
            or first_row == self.labels
        ):
            return False
        # PyMuPDF can label the first data row (or an illustration caption)
        # as the header when a table continues across a page break.
        return bool(
            re.fullmatch(r"\d{1,3}[.)]?", first_row[0])
            or any(re.match(r"^\d{1,2}[./]\d{1,2}\b", c) for c in first_row[1:])
            or (
                sum(bool(c) for c in first_row) == 1
                and any(
                    c.lower().startswith(("рисунок", "рис.", "продолжение"))
                    for c in first_row
                )
            )
        )


def relative(page: pymupdf.Page, rect: tuple[float, ...]) -> Box:
    r = pymupdf.Rect(rect) * page.rotation_matrix
    return (
        max(0.0, min(1.0, r.x0 / page.rect.width)),
        max(0.0, min(1.0, r.y0 / page.rect.height)),
        max(0.0, min(1.0, r.x1 / page.rect.width)),
        max(0.0, min(1.0, r.y1 / page.rect.height)),
    )


def scan_fragments(
    image_path: Path,
    page_no: int,
    cache: Path,
    stats: Stats,
    check_1995: bool = False,
    *,
    allow_spreads: bool = True,
) -> Iterator[Fragment]:
    with Image.open(image_path) as image:
        w, h = image.size
        halves = (
            [("L", 0, w // 2), ("R", w // 2, w)]
            if allow_spreads and w > h
            else [(None, 0, w)]
        )
        for half, left, right in halves:
            with BytesIO() as stream:
                image.crop((left, 0, right, h)).save(stream, format="PNG")
                response = ocr.recognize(stream.getvalue(), cache)
            if response is None:
                if page_no not in stats.skipped_ocr:
                    stats.skipped_ocr.append(page_no)
                continue
            lines = ocr.lines(response, right - left, h)
            printed = next(
                (
                    s.strip()
                    for s, box, _ in reversed(lines)
                    if box[3] >= 0.8 and s.strip().isdecimal()
                ),
                None,
            )
            if check_1995 and half and printed is not None:
                expected = 2 * page_no - 4 + int(half == "R")
                if expected > 0 and int(printed) != expected:
                    stats.page_number_mismatches.append(
                        {
                            "pdf_page": page_no,
                            "half": half,
                            "expected": expected,
                            "recognized": printed,
                        }
                    )
            for text, (x0, y0, x1, y1), confidence in lines:
                if not text.strip():
                    continue
                bbox = (
                    (left + x0 * (right - left)) / w,
                    y0,
                    (left + x1 * (right - left)) / w,
                    y1,
                )
                if (
                    confidence is not None
                    and confidence < 0.7
                    and page_no not in stats.low_ocr
                ):
                    stats.low_ocr.append(page_no)
                yield Fragment(
                    "paragraph",
                    {"pdf_page": page_no, "half": half, "printed_page": printed},
                    text,
                    "ocr",
                    confidence,
                    [bbox],
                )


def fragments(path: Path, pages: Path, cache: Path, stats: Stats) -> Iterator[Fragment]:
    pages.mkdir(parents=True, exist_ok=True)
    catalog = Path(__file__).resolve().parents[2] / "contracts/fixtures/documents.json"
    books = json.loads(catalog.read_text(encoding="utf-8"))
    book: dict[str, str | int | None] = next(
        (b for b in books if b["filename"].casefold() == path.name.casefold()), {}
    )
    with pymupdf.open(path) as document:
        stats.pages = len(document)
        previous = None
        for number, page in enumerate(document, start=1):
            png = pages / f"{number}.png"
            page.get_pixmap(dpi=200, alpha=False).save(png)
            if len(page.get_text().strip()) < 50:
                stats.scans += 1
                yield from scan_fragments(
                    png,
                    number,
                    cache,
                    stats,
                    book.get("year") == 1995,
                    allow_spreads=book.get("layout") != "single",
                )
                continue

            blocks = page.get_text(
                "dict",
                flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES,
                sort=True,
            )["blocks"]
            footer = [
                ("".join(s["text"] for s in ln["spans"])).strip()
                for b in blocks
                for ln in b.get("lines", [])
                if relative(page, ln["bbox"])[1] > 0.85
            ]
            printed = next((s for s in reversed(footer) if s.isdecimal()), None)
            locator = {"pdf_page": number, "half": None, "printed_page": printed}
            areas = []
            ordered = []
            for table_index, table in enumerate(page.find_tables().tables):
                stats.tables += 1
                areas.append(pymupdf.Rect(table.bbox))
                rows = [
                    [" ".join((v or "").split()) for v in cells]
                    for cells in table.extract()
                ]
                if not rows:
                    continue
                rect = relative(page, table.bbox)
                if (
                    table_index == 0
                    and previous is not None
                    and previous.continues(number, rect, rows[0])
                ):
                    headers, first = previous.labels, 0
                else:
                    headers = [" ".join((s or "").split()) for s in table.header.names]
                    first = 0 if table.header.external else 1
                    if table.header.external:
                        areas.append(pymupdf.Rect(table.header.bbox))
                previous = TableHeader(headers, number, rect[0], rect[2])
                for n, clean in enumerate(rows[first:], start=first):
                    value = row_text(headers, clean)
                    if value:
                        box = relative(page, table.rows[n].bbox)
                        spans = []
                        offset = 0
                        for col, cell in enumerate(clean):
                            if not cell:
                                continue
                            label = (
                                headers[col]
                                if col < len(headers) and headers[col]
                                else f"Столбец {col + 1}"
                            )
                            start = offset + len(label) + 2
                            end = start + len(cell)
                            bounds = table.rows[n].cells[col] or table.rows[n].bbox
                            spans.append((start, end, relative(page, bounds)))
                            offset = end + 2
                        ordered.append(
                            (
                                box[1],
                                box[0],
                                Fragment(
                                    "table_row",
                                    locator,
                                    value,
                                    boxes=[box],
                                    spans=spans,
                                ),
                            )
                        )
            for block in blocks:
                lines = []
                boxes = []
                for line in block.get("lines", []):
                    r = pymupdf.Rect(line["bbox"])
                    center = (r.tl + r.br) / 2
                    if any(center in area for area in areas):
                        continue
                    s = "".join(span["text"] for span in line["spans"])
                    if s.strip():
                        lines.append(s)
                        boxes.append(relative(page, line["bbox"]))
                if lines:
                    ordered.append(
                        (
                            boxes[0][1],
                            boxes[0][0],
                            Fragment(
                                "paragraph", locator, "\n".join(lines), boxes=boxes
                            ),
                        )
                    )
            for _, _, item in sorted(ordered, key=lambda v: (v[0], v[1])):
                yield item
