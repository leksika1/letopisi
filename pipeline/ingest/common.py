from dataclasses import dataclass, field
from typing import TypeAlias

Box: TypeAlias = tuple[float, float, float, float]
Locator: TypeAlias = dict[str, str | int | None]


@dataclass
class Fragment:
    kind: str
    locator: Locator
    text: str
    source: str = "text_layer"
    confidence: float | None = None
    boxes: list[Box] = field(default_factory=list)
    spans: list[tuple[int, int, Box]] = field(default_factory=list)


@dataclass
class Stats:
    pages: int = 0
    scans: int = 0
    chunks: int = 0
    tables: int = 0
    skipped_ocr: list[int] = field(default_factory=list)
    low_ocr: list[int] = field(default_factory=list)
    page_number_mismatches: list[dict[str, int | str]] = field(default_factory=list)


def row_text(headers: list[str], cells: list[str]) -> str:
    return "; ".join(
        f"{headers[n] if n < len(headers) and headers[n] else f'Столбец {n + 1}'}: {cell}"
        for n, cell in enumerate(cells)
        if cell
    )
