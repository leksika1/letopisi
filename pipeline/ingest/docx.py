from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .common import Fragment, Stats, row_text


def fragments(path: Path, stats: Stats) -> Iterator[Fragment]:
    para = table = 0
    for node in Document(str(path)).iter_inner_content():
        if isinstance(node, Paragraph):
            if node.text.strip():
                yield Fragment("paragraph", {"para": para}, node.text)
            para += 1
        elif isinstance(node, Table):
            stats.tables += 1
            rows = [[cell.text.strip() for cell in row.cells] for row in node.rows]
            for index, cells in enumerate(rows[1:], start=1):
                if text := row_text(rows[0], cells):
                    yield Fragment("table_row", {"table": table, "row": index}, text)
            table += 1
