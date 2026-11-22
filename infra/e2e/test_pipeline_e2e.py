"""Exercise the shipped CLIs and serve the resulting source bytes through FastAPI."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw
from playwright.sync_api import Browser, expect

from infra.e2e.browser import browser_page

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "pipeline/tests/data/llm_mock/responses.json"
QUOTE = (
    "В лагере у подножия Калкупи в июле наблюдали красно-\n"
    "серых полевок (Myodes rufocanus)"
)


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def cli(
    root: Path,
    data: Path,
    *args: str,
    database_url: str = "",
    cache_model: str | None = None,
) -> str:
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(root),
        "DATA_DIR": str(data),
        "LETOPISI_DATA_DIR": str(data),
        "LETOPISI_RAW_DIR": str(data / "raw"),
        "LLM_BACKEND": "mock",
        "LLM_MOCK_FILE": str(MOCK),
        "LLM_CACHE_DIR": str(data / "llm_cache"),
        "YC_API_KEY": "",
        "YC_FOLDER_ID": "",
        "DATABASE_URL": database_url,
    }
    if cache_model is not None:
        # An empty credential pair disables remote requests. The local URL
        # also keeps a future backend change away from the provider.
        env.update(
            LLM_BACKEND="yandex",
            LLM_MODEL=cache_model,
            LLM_BASE_URL="http://127.0.0.1:1/v1",
        )
    result = subprocess.run(
        [sys.executable, *args],
        cwd=root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=600 if cache_model is not None else 90,
        check=False,
    )
    if result.returncode:
        diagnostic = result.stderr + "\n" + result.stdout
        if cache_model is not None:
            diagnostic = diagnostic.replace(cache_model, "<release model>")
        pytest.fail(f"{args}\n{diagnostic}")
    return result.stdout


def test_csv_to_chunk(tmp_path: Path) -> None:
    source = tmp_path / "Учёты.csv"
    source.write_text("Вид;Маршрут 2\n\nЛось;7\n", encoding="utf-8-sig")
    cli(ROOT, tmp_path, "-m", "pipeline.ingest", str(source), "--document-id", "44")
    (row,) = jsonl(tmp_path / "chunks/44.jsonl")
    assert row["text"] == "Вид: Лось; Маршрут 2: 7"
    assert row["locator"] == {"sheet": "Учёты", "row": 3}
    assert row["lines"] is None


def test_docx_retains_observation_in_nested_table(tmp_path: Path) -> None:
    source = tmp_path / "nested.docx"
    doc = Document()
    outer = doc.add_table(rows=2, cols=1)
    outer.cell(0, 0).text = "Полевой журнал"
    outer.cell(1, 0).text = "Записи на маршруте"
    inner = outer.cell(1, 0).add_table(rows=2, cols=1)
    inner.cell(0, 0).text = "Наблюдение"
    observation = "У ручья отмечены три лося"
    inner.cell(1, 0).text = observation
    doc.save(str(source))
    cli(ROOT, tmp_path, "-m", "pipeline.ingest", str(source), "--document-id", "45")
    chunks = jsonl(tmp_path / "chunks/45.jsonl")
    assert any(observation in chunk["text"] for chunk in chunks)


def test_scan_replays_cached_ocr_without_credentials(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    with Image.new("RGB", (100, 140), "white") as raster:
        ImageDraw.Draw(raster).text((10, 25), "Moose", fill="black")
        stream = BytesIO()
        raster.save(stream, format="PNG")
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=100, height=140)
        page.insert_image(page.rect, stream=stream.getvalue())
        pdf.save(path)
    args = ("-m", "pipeline.ingest", str(path), "--document-id", "46")
    cli(ROOT, tmp_path, *args)
    stats = tmp_path / "chunks/46.stats.json"
    assert json.loads(stats.read_text(encoding="utf-8"))["skipped_ocr"] == [1]
    assert jsonl(tmp_path / "chunks/46.jsonl") == []

    with Image.open(tmp_path / "pages/46/1.png") as rendered:
        png = BytesIO()
        rendered.save(png, format="PNG")
    cache = tmp_path / "ocr_cache"
    cache.mkdir()
    key = hashlib.sha256(png.getvalue()).hexdigest()
    line = {
        "text": "Moose",
        "confidence": 0.96,
        "boundingBox": {"vertices": [{"x": 10, "y": 25}, {"x": 50, "y": 40}]},
    }
    response = {
        "textAnnotation": {"width": 100, "height": 140, "blocks": [{"lines": [line]}]}
    }
    (cache / f"{key}.json").write_text(json.dumps(response), encoding="utf-8")
    cli(ROOT, tmp_path, *args)
    assert json.loads(stats.read_text(encoding="utf-8"))["skipped_ocr"] == []
    (chunk,) = jsonl(tmp_path / "chunks/46.jsonl")
    assert chunk["text_source"] == "ocr" and chunk["ocr_confidence"] == 0.96
    assert chunk["locator"] == {"pdf_page": 1, "half": None, "printed_page": None}
    assert chunk["text"] == chunk["lines"][0]["text"] == "Moose"
    assert chunk["lines"][0]["bbox"] == pytest.approx([0.1, 25 / 140, 0.5, 40 / 140])


def make_sources(raw: Path) -> dict[int, Path]:
    raw.mkdir(parents=True, exist_ok=True)
    files = {
        41: raw / "field-notes.pdf",
        42: raw / "journal.docx",
        43: raw / "counts.xlsx",
    }
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        # Embed the bundled font to make the Cyrillic text searchable on any OS.
        page.insert_font(fontname="FieldNotes", fontbuffer=pymupdf.Font("helv").buffer)
        for y, line in (
            (55, "8.5 Млекопитающие"),
            (79, "Валовой Е.В."),
            (125, QUOTE),
            (813, "42"),
        ):
            page.insert_text((35, y), line, fontname="FieldNotes", fontsize=12)
        pdf.save(files[41])

    doc = Document()
    doc.add_paragraph()
    doc.add_paragraph(QUOTE)
    table = doc.add_table(rows=2, cols=2)
    for row, values in zip(
        table.rows, [("Вид", "Маршрут 2"), ("Лось", "4")], strict=True
    ):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    doc.save(str(files[42]))

    book = Workbook()
    sheet = book.active
    sheet.title = "Маршруты"
    sheet.append(["Вид", "Маршрут 2"])
    sheet.append([])
    sheet.append(["Лось", 7])
    book.save(files[43])
    book.close()
    return files


def prepare_catalog(root: Path, files: dict[int, Path]) -> None:
    # Both run and build_stand_bundle find their catalog relative to __file__.
    # The bundle builder also resolves data/ this way. Use a private checkout
    # with accurate document IDs, file hashes and page counts for these books.
    shutil.copytree(
        ROOT / "pipeline",
        root / "pipeline",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(ROOT / "contracts", root / "contracts")
    shutil.copytree(ROOT / "dictionaries", root / "dictionaries")
    catalog = root / "contracts/fixtures/documents.json"
    documents = json.loads(catalog.read_text(encoding="utf-8"))
    for doc_id, source in files.items():
        pdf = source.suffix == ".pdf"
        documents.append(
            {
                "id": doc_id,
                "title": source.stem,
                "book_no": None,
                "year": 2020,
                "filename": source.name,
                "format": source.suffix[1:],
                "pages": 1 if pdf else None,
                "has_text_layer": pdf,
                "layout": "single" if pdf else None,
                "status": "done",
                "error": None,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "created_at": "2020-12-31T00:00:00Z",
            }
        )
    catalog.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")


def test_llm_cannot_anchor_an_invented_sentence_to_one_word(tmp_path: Path) -> None:
    source = tmp_path / "journal.docx"
    doc = Document()
    doc.add_paragraph(QUOTE)
    doc.add_paragraph("полёвки")
    doc.save(str(source))
    checkout = tmp_path / "checkout"
    prepare_catalog(checkout, {47: source})
    data = checkout / "data"
    cli(checkout, data, "-m", "pipeline.ingest", str(source), "--document-id", "47")
    cli(checkout, data, "-m", "pipeline.run", "--document", "47")
    observations = jsonl(data / "observations/47.jsonl")
    matches = [o for o in observations if o["object_raw"] == "полёвки"]
    assert matches, "The checked-in LLM mock must supply the invented sentence"
    for obs in matches:
        assert "quote_not_found" in obs["flags"], (obs["quote"], obs["locator"])
        assert obs["quote_span"] is None and obs["bboxes"] == []


def test_extract_bundle_load_and_follow_source(
    tmp_path: Path, pipeline_service: tuple[httpx.Client, str, Path], browser: Browser
) -> None:
    api, database_url, storage = pipeline_service
    files = make_sources(storage / "raw")
    checkout = tmp_path / "checkout"
    prepare_catalog(checkout, files)
    data = checkout / "data"
    extracted: dict[int, list[dict[str, Any]]] = {}
    chunks: dict[int, dict[str, Any]] = {}
    for doc_id, source in files.items():
        cli(
            checkout,
            data,
            "-m",
            "pipeline.ingest",
            str(source),
            "--document-id",
            str(doc_id),
        )
        cli(checkout, data, "-m", "pipeline.run", "--document", str(doc_id))
        report_path = data / f"observations/{doc_id}.report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["llm_backend"] == "mock" and report["llm_error"] is None
        assert report["invalid_dropped"] == 0
        extracted[doc_id] = jsonl(data / f"observations/{doc_id}.jsonl")
        chunks.update({c["id"]: c for c in jsonl(data / f"chunks/{doc_id}.jsonl")})
        assert len(extracted[doc_id]) == report["observations"] > 0
        if doc_id in (41, 42):
            assert report["llm_usage"]["calls"] == report["llm_windows"] > 0

    (pdf_obs,) = [o for o in extracted[41] if o["quote"] == QUOTE]
    assert pdf_obs["locator"] == {"pdf_page": 1, "half": None, "printed_page": "42"}
    assert pdf_obs["date_from"] == "2020-07-01" and pdf_obs["place_id"] == 4
    assert pdf_obs["person_ids"] == [8] and len(pdf_obs["bboxes"]) == 2
    (bad,) = [o for o in extracted[41] if "quote_not_found" in o["flags"]]
    assert bad["quote_span"] is None and bad["bboxes"] == []
    (docx_obs,) = [o for o in extracted[42] if o["quote"] == QUOTE]
    assert docx_obs["locator"] == {"para": 1} and docx_obs["bboxes"] == []
    for doc_id, locator, number in (
        (42, {"table": 0, "row": 1}, 4),
        (43, {"sheet": "Маршруты", "row": 3}, 7),
    ):
        (count,) = [o for o in extracted[doc_id] if o["taxon_id"] == 16]
        assert count["locator"] == locator and count["value_num"] == number
        assert count["indicator"] == "Маршрут 2" and count["bboxes"] == []

    bundle = tmp_path / "bundle"
    stats = json.loads(
        cli(
            checkout,
            data,
            "-m",
            "pipeline.devtools.build_stand_bundle",
            "--out",
            str(bundle),
        )
    )
    assert stats["documents_with_run"] == len(files)
    inserted = json.loads(
        cli(ROOT, data, "pipeline/load.py", str(bundle), database_url=database_url)
    )
    assert inserted["observations"] == stats["observations"]
    repeated = json.loads(
        cli(ROOT, data, "pipeline/load.py", str(bundle), database_url=database_url)
    )
    assert all(count == 0 for count in repeated.values())

    search = api.get("/api/observations", params={"document_id": 41, "q": "яблоки"})
    search.raise_for_status()
    assert [item["id"] for item in search.json()["items"]] == [pdf_obs["id"]]

    for doc_id, source in files.items():
        lookup = api.get("/api/observations", params={"document_id": doc_id})
        lookup.raise_for_status()
        assert {item["id"] for item in lookup.json()["items"]} == {
            o["id"] for o in extracted[doc_id]
        }
        original = api.get(f"/api/documents/{doc_id}/file")
        original.raise_for_status()
        assert original.content == source.read_bytes()
        for obs in extracted[doc_id]:
            detail = api.get(f"/api/observations/{obs['id']}")
            detail.raise_for_status()
            record = detail.json()
            assert (
                record["document"]["sha256"]
                == hashlib.sha256(original.content).hexdigest()
            )
            chunk = record["chunk"]
            assert chunk["id"] == obs["chunk_id"]
            assert chunk["text"] == chunks[chunk["id"]]["text"]
            for field in ("locator", "quote", "quote_span", "bboxes"):
                assert record["observation"][field] == obs[field]
            if obs["quote_span"]:
                start, end = obs["quote_span"]
                assert chunk["text"][start:end] == obs["quote"]
            if obs["id"] == pdf_obs["id"]:
                assert record["section"]["code"] == "8.5"
                assert record["section"]["author_person_ids"] == [8]
                assert [line["text"] for line in chunk["lines"]] == QUOTE.splitlines()
                for line, box in zip(chunk["lines"], obs["bboxes"], strict=True):
                    assert line["bbox"] == pytest.approx(box, abs=0.0001)

    png = api.get("/api/documents/41/pages/1.png", params={"dpi": 100})
    png.raise_for_status()
    assert png.headers["content-type"] == "image/png"
    with Image.open(BytesIO(png.content)) as image:
        for x0, y0, x1, y1 in pdf_obs["bboxes"]:
            box = (
                x0 * image.width,
                y0 * image.height,
                x1 * image.width,
                y1 * image.height,
            )
            darkest = image.crop(box).convert("L").getextrema()[0]
            assert isinstance(darkest, (int, float)) and darkest < 100

    with browser_page(browser, str(api.base_url)) as page:
        with page.expect_response(
            lambda r: "/api/documents/41/pages/1.png" in r.url
        ) as fetch:
            page.goto(f"/observations/{pdf_obs['id']}")
        assert fetch.value.status == 200
        quote = page.get_by_test_id("quote-highlight")
        expect(quote).to_be_visible()
        assert quote.text_content() == pdf_obs["quote"]
        view = page.get_by_test_id("page-view")
        rectangles = view.locator("[data-bbox]")
        expect(rectangles).to_have_count(len(pdf_obs["bboxes"]))
        for rect, box in zip(rectangles.all(), pdf_obs["bboxes"], strict=True):
            coordinates = rect.get_attribute("data-bbox")
            assert coordinates is not None
            assert [float(v) for v in coordinates.split(",")] == pytest.approx(box)
        img = view.get_by_role("img", name="Страница PDF 1")
        with Image.open(BytesIO(fetch.value.body())) as served:
            expect(img).to_have_js_property("naturalWidth", served.width)
            expect(img).to_have_js_property("naturalHeight", served.height)
        before = img.bounding_box()
        assert before and before["width"] > 0
        page.get_by_role("button", name="Увеличить").click()
        expect(page.get_by_text("150%", exact=True)).to_be_visible()
        after = img.bounding_box()
        assert after and after["width"] == pytest.approx(before["width"] * 1.5, abs=2)
