"""Replay the real core CLI with a small PDF and the published 2020 book."""

import gzip
import json
import shutil
import tarfile
from pathlib import Path

import pymupdf

from infra.e2e.test_pipeline_e2e import QUOTE, ROOT, cli, jsonl, prepare_catalog


def test_quick_chunks_pdf_to_anchored_observations(tmp_path: Path) -> None:
    source = tmp_path / "field-notes.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_font(fontname="FieldNotes", fontbuffer=pymupdf.Font("helv").buffer)
        page.insert_text((40, 80), QUOTE, fontname="FieldNotes")
        page.insert_text((280, 812), "12")
        for x in (40, 180, 320):
            page.draw_line((x, 160), (x, 250))
        for y in (160, 190, 220, 250):
            page.draw_line((40, y), (320, y))
        for row, values in enumerate(
            [("Вид", "Численность"), ("Лось", "7"), ("Бурый медведь", "3")]
        ):
            for x, value in zip((45, 185), values, strict=True):
                page.insert_text((x, 180 + row * 30), value, fontname="FieldNotes")
        pdf.save(source)

    checkout = tmp_path / "checkout"
    prepare_catalog(checkout, {48: source})
    data = checkout / "data"
    cli(
        checkout,
        data,
        "-m",
        "pipeline.devtools.quick_chunks",
        str(source),
        "--document-id",
        "48",
        "--pages",
        "1-1",
        "--out",
        str(data / "chunks"),
    )
    chunks = jsonl(data / "chunks/48.jsonl")
    geometry = {row["chunk_id"]: row for row in jsonl(data / "chunks/48.lines.jsonl")}
    assert [c["ord"] for c in chunks] == list(range(len(chunks)))
    assert {c["id"] for c in chunks} == geometry.keys()
    locator = {"pdf_page": 1, "half": None, "printed_page": "12"}
    assert all(
        c["locator"] == locator and c["text_source"] == "text_layer" for c in chunks
    )
    (paragraph,) = [c for c in chunks if c["kind"] == "paragraph"]
    assert paragraph["text"] == QUOTE
    table = [c for c in chunks if c["kind"] == "table_row"]
    assert [c["text"] for c in table] == [
        "Вид: Лось; Численность: 7",
        "Вид: Бурый медведь; Численность: 3",
    ]
    for chunk, expected in [
        (paragraph, QUOTE.splitlines()),
        (table[0], ["Лось", "7"]),
        (table[1], ["Бурый медведь", "3"]),
    ]:
        meta = geometry[chunk["id"]]
        assert meta["pdf_page"] == 1
        assert [
            chunk["text"][start:end] for start, end, *_ in meta["lines"]
        ] == expected
        left, top, right, bottom = meta["bbox"]
        assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1
        for _, _, x0, y0, x1, y1 in meta["lines"]:
            assert left <= x0 < x1 <= right and top <= y0 < y1 <= bottom

    cli(checkout, data, "-m", "pipeline.run", "--document", "48")
    observations = jsonl(data / "observations/48.jsonl")
    (observed,) = [o for o in observations if o["quote"] == QUOTE]
    assert observed["locator"] == locator and observed["chunk_id"] == paragraph["id"]
    assert observed["quote_span"] == [0, len(QUOTE)]
    assert observed["bboxes"] == [
        line[2:] for line in geometry[paragraph["id"]]["lines"]
    ]
    counts = [o for o in observations if o["indicator"] == "Численность"]
    assert [(o["chunk_id"], o["value_num"]) for o in counts] == [
        (table[0]["id"], 7),
        (table[1]["id"], 3),
    ]


def test_full_book_replay_from_published_cache(tmp_path: Path) -> None:
    release = ROOT / "release/book27_2020"
    with gzip.open(
        release / "observations_3.report.json.gz", "rt", encoding="utf-8"
    ) as source:
        reference = json.load(source)
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    for name in ("3.jsonl", "3.lines.jsonl"):
        with (
            gzip.open(release / f"chunks_{name}.gz", "rb") as source,
            (chunks / name).open("wb") as target,
        ):
            shutil.copyfileobj(source, target)
    with tarfile.open(release / "llm_cache.tar.gz", "r:gz") as archive:
        archive.extractall(tmp_path, filter="data")

    cli(
        ROOT,
        tmp_path,
        "-m",
        "pipeline.run",
        "--document",
        "3",
        cache_model=reference["model"],
    )
    report = json.loads(
        (tmp_path / "observations/3.report.json").read_text(encoding="utf-8")
    )
    assert report["llm_error"] is None
    assert (
        report["llm_windows"]
        == report["llm_usage"]["cached"]
        == reference["llm_windows"]
        == 1042
    )
    assert (
        report["llm_usage"]["calls"]
        == report["llm_usage"]["errors"]
        == report["llm_usage"]["retries"]
        == 0
    )
    assert (
        report["llm_usage"]["prompt_tokens"]
        == report["llm_usage"]["completion_tokens"]
        == 0
    )
    assert report["chunks_total"] == reference["chunks_total"] == 8761
    assert (
        report["observations"] == reference["observations"] == 6273
        and report["invalid_dropped"] == 0
    )
    assert report["by_strategy"] == reference["by_strategy"]
    # Later normalization has changed a few labels and publication metadata.
    # Every replayed record must still describe the same source observation.
    fields = (
        "id",
        "document_id",
        "chunk_id",
        "section_id",
        "locator",
        "quote",
        "quote_span",
        "bboxes",
        "object_raw",
        "date_raw",
        "value_raw",
        "obs_year",
    )
    with (
        gzip.open(
            release / "observations_3.jsonl.gz", "rt", encoding="utf-8"
        ) as expected,
        (tmp_path / "observations/3.jsonl").open(encoding="utf-8") as actual,
    ):
        for old, new in zip(
            map(json.loads, expected), map(json.loads, actual), strict=True
        ):
            for field in fields:
                assert new[field] == old[field], (old["id"], field)
