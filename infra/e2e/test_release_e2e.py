"""Install the published 2020 volume from its archives into an empty live service."""

import gzip
import json
import shutil
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from playwright.sync_api import Browser, expect
from sqlalchemy import create_engine, text

from infra.e2e.browser import browser_page
from infra.e2e.test_pipeline_e2e import ROOT, cli, prepare_catalog


@pytest.fixture
def installed_release(
    pipeline_service: tuple[httpx.Client, str, Path],
) -> tuple[httpx.Client, str, Path]:
    api, database_url, data = pipeline_service
    prepare_catalog(data.parent, {})
    release = ROOT / "release/book27_2020"
    for archive in sorted([*release.glob("*.jsonl.gz"), *release.glob("*.json.gz")]):
        kind, filename = archive.stem.split("_", 1)
        target = data / kind / filename
        target.parent.mkdir(exist_ok=True)
        with gzip.open(archive, "rb") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)

    bundle = data / "stand_bundle"
    cli(
        data.parent,
        data,
        "-m",
        "pipeline.devtools.build_stand_bundle",
        "--out",
        str(bundle),
    )
    for attempt in range(2):
        counts = json.loads(
            cli(ROOT, data, "pipeline/load.py", str(bundle), database_url=database_url)
        )
        if attempt == 0:
            assert counts["observations"] == 6278 and counts["chunks"] == 8763
        else:
            assert counts and all(value == 0 for value in counts.values()), counts

    return api, database_url, data


def test_install_release_twice_and_read_its_source(
    installed_release: tuple[httpx.Client, str, Path],
) -> None:
    api, _, data = installed_release
    search = api.get("/api/observations", params={"document_id": 3, "page_size": 1})
    search.raise_for_status()
    assert search.json()["total"] == 6273

    with (data / "observations/3.jsonl").open(encoding="utf-8") as rows:
        expected = next(row for row in map(json.loads, rows) if row["quote_span"])
    detail = api.get(f"/api/observations/{expected['id']}")
    detail.raise_for_status()
    card = detail.json()
    assert card["document"]["id"] == 3
    assert card["chunk"]["id"] == expected["chunk_id"]
    start, end = expected["quote_span"]
    assert card["observation"]["quote_span"] == [start, end]
    assert (
        card["chunk"]["text"][start:end]
        == card["observation"]["quote"]
        == expected["quote"]
    )

    report = data / "quality.json"
    cli(
        data.parent,
        data,
        "-m",
        "pipeline.eval.evaluate",
        "--gold",
        str(ROOT / "gold/sanity_2020.csv"),
        "--observations",
        str(data / "observations/3.jsonl"),
        "--out",
        str(report),
    )
    quality = json.loads(report.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "contracts/observation.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/Quality"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    ).validate(quality)
    assert quality["is_placeholder"] is False
    assert quality["n_gold_records"] == 22 and quality["n_extracted_records"] > 0
    assert quality["record_level"]["f1"] > 0
    assert [item["document_id"] for item in quality["by_document"]] == [3]


def test_map_includes_all_locations_in_released_book(
    installed_release: tuple[httpx.Client, str, Path],
    browser: Browser,
) -> None:
    api, database_url, _ = installed_release
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            total = conn.scalar(
                text(
                    "SELECT count(DISTINCT place_id) FROM observations WHERE document_id=3 AND status <> 'rejected'"
                )
            )
    finally:
        engine.dispose()
    assert total and total > 4
    with browser_page(browser, str(api.base_url)) as page:
        page.goto("/?tab=place&document_id=3&sort=date_from&order=asc")
        caption = page.get_by_text("На карте — места из текущей выдачи", exact=False)
        expect(caption).to_contain_text(f"из {total})")
