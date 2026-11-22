import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pymupdf
import pytest
from docx import Document
from openpyxl import Workbook

from backend.app.contract import validate
from pipeline.ingest import ingest, ocr


def read_chunks(data: Path, doc: int) -> list[dict]:
    chunks = [
        json.loads(s)
        for s in (data / "chunks" / f"{doc}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for row in chunks:
        validate("Chunk", row)
        for line in row["lines"] or []:
            assert row["text"][line["start"] : line["end"]] == line["text"]
    return chunks


def test_text_and_table(tmp_path: Path) -> None:
    path = tmp_path / "text.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.insert_text(
            (20, 35), "A paragraph about samples collected at the river and lake."
        )
        for x in (30, 150, 260):
            page.draw_line((x, 70), (x, 160))
        for y in (70, 100, 130, 160):
            page.draw_line((30, y), (260, y))
        for i, (a, b) in enumerate(
            [("Species", "Count"), ("Moose", "7"), ("Bear", "2")]
        ):
            page.insert_text((38, 88 + 30 * i), a)
            page.insert_text((158, 88 + 30 * i), b)
        pdf.save(path)
    stats = ingest(path, 8, tmp_path)
    rows = read_chunks(tmp_path, 8)
    assert stats.scans == 0 and stats.tables == 1
    assert (tmp_path / "pages/8/1.png").is_file()
    row = next(r for r in rows if r["kind"] == "table_row" and "Moose" in r["text"])
    assert row["text"] == "Species: Moose; Count: 7"
    assert row["lines"][0]["bbox"] != row["lines"][1]["bbox"]
    assert sum("Moose" in r["text"] for r in rows) == 1


def test_continued_table_reuses_column_names(tmp_path: Path) -> None:
    path = tmp_path / "continued.pdf"
    with pymupdf.open() as doc:
        for top, entries in [
            (120, [("No", "Species"), ("1.", "Moose")]),
            (20, [("2.", "Bear"), ("3.", "Hare")]),
        ]:
            page = doc.new_page(width=300, height=450)
            page.insert_text(
                (15, 300),
                "A separate paragraph with text about animals in the reserve.",
            )
            for x in (25, 85, 200):
                page.draw_line((x, top), (x, top + 60))
            for y in (top, top + 30, top + 60):
                page.draw_line((25, y), (200, y))
            for i, (number, name) in enumerate(entries):
                page.insert_text((30, top + 18 + 30 * i), number)
                page.insert_text((90, top + 18 + 30 * i), name)
        doc.save(path)
    ingest(path, 6, tmp_path)
    rows = [r["text"] for r in read_chunks(tmp_path, 6) if r["kind"] == "table_row"]
    assert rows == [
        "No: 1.; Species: Moose",
        "No: 2.; Species: Bear",
        "No: 3.; Species: Hare",
    ]


@pytest.mark.parametrize(
    "filename,spread", [("scan.pdf", True), ("book27_2020.pdf", False)]
)
def test_spread_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, spread: bool
) -> None:
    from io import BytesIO

    from PIL import Image

    png = BytesIO()
    Image.new("RGB", (400, 200), "white").save(png, format="PNG")
    path = tmp_path / filename
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=400, height=200)
        p.insert_image(p.rect, stream=png.getvalue())
        pdf.save(path)

    def answer(num: str) -> dict:
        box = {"vertices": [{"x": "0", "y": "0"}, {"x": "100", "y": "100"}]}
        return {
            "textAnnotation": {
                "width": "100",
                "height": "100",
                "blocks": [
                    {
                        "lines": [
                            {
                                "text": "наблюдение",
                                "confidence": 0.45,
                                "boundingBox": box,
                            },
                            {"text": num, "boundingBox": box},
                        ]
                    }
                ],
            }
        }

    mock = Mock(side_effect=[answer("76"), answer("77")])
    monkeypatch.setattr(ocr, "recognize", mock)
    stats = ingest(path, 7, tmp_path)
    rows = read_chunks(tmp_path, 7)
    assert (
        mock.call_count == (2 if spread else 1)
        and stats.scans == 1
        and stats.low_ocr == [1]
    )
    for r in rows:
        if not spread:
            assert r["locator"]["half"] is None and r["lines"][0]["bbox"] == [
                0,
                0,
                1,
                1,
            ]
            continue
        left = r["locator"]["half"] == "L"
        assert r["locator"]["printed_page"] == ("76" if left else "77")
        assert r["lines"][0]["bbox"][0] == pytest.approx(0 if left else 0.5, abs=0.001)
        assert r["lines"][0]["bbox"][2] == pytest.approx(0.5 if left else 1, abs=0.001)


def test_ocr_retry_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YC_API_KEY", "unit-test")
    monkeypatch.setenv("YC_FOLDER_ID", "test-folder")
    result = {"result": {"textAnnotation": {"blocks": []}, "page": "0"}}
    req = httpx.Request("POST", ocr.URL)
    post = Mock(
        side_effect=[
            httpx.Response(429, request=req),
            httpx.Response(503, request=req),
            httpx.Response(200, json=result, request=req),
        ]
    )
    monkeypatch.setattr(ocr.httpx, "post", post)
    monkeypatch.setattr(ocr.time, "sleep", lambda seconds: None)
    assert (
        ocr.recognize(b"test", tmp_path) == ocr.recognize(b"test", tmp_path) == result
    )
    assert post.call_count == 3
    assert ocr.lines(result, 100, 100) == []
    assert post.call_args.kwargs["json"]["languageCodes"] == ["ru", "en"]


def test_1995_printed_number_disagrees_with_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from pipeline.ingest.common import Stats
    from pipeline.ingest.pdf import scan_fragments

    img = tmp_path / "40.png"
    Image.new("RGB", (200, 100)).save(img)
    monkeypatch.setattr(ocr, "recognize", lambda *args: {})
    replies = iter(
        [
            [("78", (0.0, 0.91, 0.1, 0.99), None)],
            [("45", (0.0, 0.2, 0.1, 0.4), None)],
        ]
    )
    monkeypatch.setattr(ocr, "lines", lambda *args: next(replies))
    stats = Stats()
    parts = list(scan_fragments(img, 40, tmp_path, stats, check_1995=True))
    assert [p.locator["printed_page"] for p in parts] == ["78", None]
    assert stats.page_number_mismatches == [
        {"pdf_page": 40, "half": "L", "expected": 76, "recognized": "78"},
    ]


def test_docx_order(tmp_path: Path) -> None:
    doc = Document()
    doc.add_paragraph("до")
    table = doc.add_table(rows=2, cols=2)
    for row, vals in zip(table.rows, [("Вид", "Число"), ("Лось", "3")], strict=True):
        for cell, s in zip(row.cells, vals, strict=True):
            cell.text = s
    doc.add_paragraph("после")
    file = tmp_path / "test.docx"
    doc.save(file)
    ingest(file, 9, tmp_path)
    rows = read_chunks(tmp_path, 9)
    assert [(r["text"], r["locator"]) for r in rows] == [
        ("до", {"para": 0}),
        ("Вид: Лось; Число: 3", {"table": 0, "row": 1}),
        ("после", {"para": 1}),
    ]


@pytest.mark.parametrize("ext", ["csv", "xlsx"])
def test_spreadsheet_coordinates(tmp_path: Path, ext: str) -> None:
    path = tmp_path / f"Учет.{ext}"
    if ext == "csv":
        path.write_text("Вид;Число\nЛось;2\n", encoding="utf-8-sig")
    else:
        book = Workbook()
        book.active.title = "Учет"
        book.active.append(["Вид", "Число"])
        book.active.append(["Лось", 2])
        book.save(path)
    ingest(path, 10, tmp_path)
    rows = read_chunks(tmp_path, 10)
    assert rows[0]["locator"] == {"sheet": "Учет", "row": 2}
    assert rows[0]["text"] == "Вид: Лось; Число: 2" and rows[0]["lines"] is None
