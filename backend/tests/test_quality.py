import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from backend.app.contract import validator
from backend.app.db import ROOT
from backend.app.main import app
from backend.build_quality import build
from pipeline.eval import evaluate as evaluator
from pipeline.load import load_fixtures


@pytest.fixture
def http(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    load_fixtures(database)
    with TestClient(app) as client:
        yield client


def get_report(http: TestClient, document_id: int | None = None) -> dict[str, Any]:
    response = http.get(
        "/api/quality", params={"document_id": document_id} if document_id else {}
    )
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    validator("Quality").validate(report)
    return report


def test_missing_and_invalid_report(http: TestClient, tmp_path: Path) -> None:
    empty = get_report(http)
    assert empty["is_placeholder"] and empty["record_level"]["f1"] is None
    assert empty["fields"] == empty["error_examples"] == empty["unsupported"] == []
    assert [r["document_id"] for r in empty["by_document"]] == [1, 2, 3]
    assert [r["document_id"] for r in get_report(http, 3)["by_document"]] == [3]
    assert get_report(http, 9999)["by_document"] == []
    for invalid in (
        "{",
        '{"record_level":{}}',
        json.dumps(empty).replace("null", "NaN"),
    ):
        (tmp_path / "quality.json").write_text(invalid, encoding="utf-8")
        response = http.get("/api/quality")
        assert (
            response.status_code == 500
            and response.json()["error"]["code"] == "internal"
        )


def test_generated_reports_match_each_document(
    http: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The core CLI reads unsupported-page evidence under ROOT/data. Use isolated
    # evidence here, independent of any locally installed archive of real PDFs.
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/observation.schema.json").write_bytes(
        (ROOT / "contracts/observation.schema.json").read_bytes()
    )
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    source = ROOT / "contracts/fixtures/observations.jsonl"
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile, delimiter=";")
        writer.writerow(
            [
                "Документ",
                "PDF-страница",
                "Наименование вида (русский)",
                "Год наблюдения",
            ]
        )
        writer.writerow(["3", 71, "Герань лесная", 2020])
        writer.writerow(["1", 40, "Северный олень", 1995])
    build(tmp_path, gold, source)
    all_books = get_report(http)
    assert all_books == json.loads((tmp_path / "quality.json").read_bytes())
    assert not all_books["is_placeholder"] and all_books["n_gold_records"] == 2
    geranium, reindeer = get_report(http, 3), get_report(http, 1)
    assert geranium["n_gold_records"] == reindeer["n_gold_records"] == 1
    assert (
        geranium["record_level"]["recall"] == 1
        and reindeer["record_level"]["recall"] == 0
    )
    assert geranium["n_extracted_records"] > 0 and reindeer["n_extracted_records"] == 0
    for doc_id, scoped in ((3, geranium), (1, reindeer)):
        assert not scoped["is_placeholder"]
        assert next(f for f in scoped["fields"] if f["field"] == "species_ru")["n"] == 1
        for key in ("by_document", "error_examples", "unsupported"):
            assert all(r["document_id"] == doc_id for r in scoped[key])
    details = tmp_path / "quality.documents.json"
    partial = json.loads(details.read_bytes())
    del partial["reports"]["1"]
    details.write_text(json.dumps(partial), encoding="utf-8")
    assert get_report(http, 1)["is_placeholder"]
    assert get_report(http, 3) == geranium
    # An older bundle cannot provide field metrics for a newer aggregate.
    overall = tmp_path / "quality.json"
    overall.write_bytes(overall.read_bytes() + b"\n")
    assert get_report(http, 3)["is_placeholder"]
    (tmp_path / "quality.documents.json").unlink()
    fallback = get_report(http, 1)
    assert fallback["is_placeholder"] and fallback["record_level"]["precision"] is None
    assert all(e["document_id"] == 1 for e in fallback["error_examples"])
    # When a single book was evaluated, the aggregate has exact field-level
    # counts for that book even without a per-document report bundle.
    other_page = {
        "document_id": 2,
        "pdf_page": 1,
        "reason": "image_only",
        "comment": None,
    }
    overall.write_text(
        json.dumps({**geranium, "unsupported": [*geranium["unsupported"], other_page]}),
        encoding="utf-8",
    )
    assert get_report(http, 3) == geranium
    assert get_report(http, 2)["unsupported"] == [other_page]
