import json
import os
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import docx, pdf, tables
from .common import Stats


def ingest(path: Path, document_id: int, data: Path) -> Stats:
    if document_id < 1:
        raise ValueError("document_id должен быть положительным")
    directory = data / "chunks"
    directory.mkdir(parents=True, exist_ok=True)
    stats = Stats()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        parts = pdf.fragments(
            path, data / "pages" / str(document_id), data / "ocr_cache", stats
        )
    elif suffix == ".docx":
        parts = docx.fragments(path, stats)
    elif suffix in {".xlsx", ".csv"}:
        parts = tables.fragments(path, stats)
    else:
        raise ValueError(f"Неизвестный формат {suffix}")
    with TemporaryDirectory(dir=directory) as tmp:
        folder = Path(tmp)
        chunks = f"{document_id}.jsonl"
        geometry = f"{document_id}.lines.jsonl"
        report = f"{document_id}.stats.json"
        with (
            (folder / chunks).open("w", encoding="utf-8") as out,
            (folder / geometry).open("w", encoding="utf-8") as coords,
        ):
            for n, part in enumerate(parts):
                # Keep IDs compatible with the core's quick_chunks input.
                pk = document_id * 1_000_000 + n + 1
                if n >= 1_000_000 or pk >= 2**63:
                    raise OverflowError(
                        "Идентификатор фрагмента выходит за пределы bigint"
                    )
                record: dict[str, Any] = {
                    "id": pk,
                    "document_id": document_id,
                    "section_id": None,
                    "ord": n,
                    "kind": part.kind,
                    "locator": part.locator,
                    "text": part.text,
                    "text_source": part.source,
                    "ocr_confidence": part.confidence,
                }
                record["lines"] = None
                if part.boxes:
                    texts = (
                        part.text.split("\n") if len(part.boxes) > 1 else [part.text]
                    )
                    lines: list[dict[str, Any]] = []
                    offset = 0
                    for value, box in zip(texts, part.boxes, strict=True):
                        end = offset + len(value)
                        lines.append(
                            {"text": value, "start": offset, "end": end, "bbox": box}
                        )
                        offset = end + 1
                    if part.spans:
                        lines = [
                            {"text": part.text[a:b], "start": a, "end": b, "bbox": box}
                            for a, b, box in part.spans
                        ]
                    record["lines"] = lines
                    x0, y0, x1, y1 = zip(*part.boxes, strict=True)
                    coords.write(
                        json.dumps(
                            {
                                "chunk_id": pk,
                                "pdf_page": part.locator["pdf_page"],
                                "bbox": [min(x0), min(y0), max(x1), max(y1)],
                                "lines": [
                                    [v["start"], v["end"], *v["bbox"]] for v in lines
                                ],
                            }
                        )
                        + "\n"
                    )
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                stats.chunks += 1
        (folder / report).write_text(
            json.dumps(asdict(stats), ensure_ascii=False), encoding="utf-8"
        )
        for name in (geometry, report, chunks):
            os.replace(folder / name, directory / name)
    return stats
