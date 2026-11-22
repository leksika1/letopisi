from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from backend.app import jobs
from backend.app.main import app
from pipeline.load import load_fixtures


def source(count: str) -> bytes:
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    for row, values in zip(
        table.rows, [("Вид", "Численность"), ("Лось", count)], strict=True
    ):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def upload(client: TestClient, original: bytes) -> int:
    response = client.post(
        "/api/documents",
        files={"file": ("учёты.docx", original)},
        data={"title": "Маршрутный учёт", "year": 2020},
    )
    assert response.status_code == 201 and response.json()["status"] == "queued"
    return int(response.json()["id"])


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YC_API_KEY", "")
    monkeypatch.setenv("YC_FOLDER_ID", "")
    monkeypatch.setenv("LLM_BACKEND", "yandex")
    return tmp_path


def test_parallel_uploads_keep_their_sources_and_review(
    database: Engine, storage: Path
) -> None:
    originals = [source("2"), source("7")]
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda book: upload(client, book), originals))
        assert len(set(ids)) == 2
        for doc_id, number, raw in zip(ids, (2, 7), originals, strict=True):
            assert client.get(f"/api/documents/{doc_id}/file").content == raw
            page = client.get(
                "/api/observations", params={"document_id": doc_id}
            ).json()
            assert page["total"] == 1
            obs = page["items"][0]
            assert obs["value_num"] == number and obs["taxon_id"] == 16
            detail = client.get(f"/api/observations/{obs['id']}").json()
            assert (
                detail["document"]["status"] == "done"
                and detail["chunk"]["document_id"] == doc_id
            )
            assert detail["chunk"]["locator"] == {"table": 0, "row": 1}
            start, end = obs["quote_span"]
            assert detail["chunk"]["text"][start:end] == obs["quote"]
            response = client.patch(
                f"/api/observations/{obs['id']}",
                json={"action": "verify", "editor": "E2E"},
            )
            assert response.is_success
            jobs.run(doc_id)
            again = client.get(
                "/api/observations", params={"document_id": doc_id}
            ).json()
            assert [(o["id"], o["status"]) for o in again["items"]] == [
                (obs["id"], "verified")
            ]


def test_failed_publication_rolls_back_and_can_be_retried(
    database: Engine, storage: Path
) -> None:
    with database.begin() as conn:
        conn.exec_driver_sql(
            "ALTER TABLE observations ADD CONSTRAINT interrupt_upload CHECK (value_num IS DISTINCT FROM 9)"
        )

    with TestClient(app) as client:
        doc_id = upload(client, source("9"))
        with database.begin() as conn:
            assert (
                conn.scalar(
                    text("SELECT status FROM documents WHERE id=:id"), {"id": doc_id}
                )
                == "failed"
            )
            for table in ("chunks", "sections", "observations"):
                assert (
                    conn.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE document_id=:id"),
                        {"id": doc_id},
                    )
                    == 0
                )
            conn.exec_driver_sql(
                "ALTER TABLE observations DROP CONSTRAINT interrupt_upload"
            )
        jobs.run(doc_id)
        result = client.get("/api/observations", params={"document_id": doc_id}).json()
        assert result["total"] == 1 and result["items"][0]["value_num"] == 9


def test_startup_resumes_interrupted_uploads(
    database: Engine, storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_fixtures(database)
    with monkeypatch.context() as patch, TestClient(app) as client:
        patch.setattr(jobs, "run", lambda doc_id: None)
        queued = upload(client, source("4"))
        processing = upload(client, source("5"))
    (storage / "raw/book03_1996.pdf").write_bytes(b"stand original, not an API upload")
    with database.begin() as conn:
        conn.execute(
            text("UPDATE documents SET status='processing' WHERE id=:id"),
            {"id": processing},
        )
    with TestClient(app) as client:
        assert client.get("/api/health").json()["db"]
    with database.connect() as conn:
        assert conn.execute(
            text("SELECT id,status FROM documents WHERE id IN (:a,:b,2) ORDER BY id"),
            {"a": queued, "b": processing},
        ).all() == [(2, "queued"), (queued, "done"), (processing, "done")]
        assert conn.execute(
            text(
                "SELECT document_id, value_num FROM observations WHERE document_id IN (:a,:b) ORDER BY document_id"
            ),
            {"a": queued, "b": processing},
        ).all() == [(queued, 4), (processing, 5)]


def test_pdf_upload_publishes_page_geometry(database: Engine, storage: Path) -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_font(fontname="FieldNotes", fontbuffer=pymupdf.Font("helv").buffer)
        page.insert_text(
            (40, 80),
            "Таблица 1. Численность животных на маршруте",
            fontname="FieldNotes",
        )
        for x in (40, 170, 300):
            page.draw_line((x, 150), (x, 210))
        for y in (150, 180, 210):
            page.draw_line((40, y), (300, y))
        for n, values in enumerate([("Вид", "Численность"), ("Лось", "6")]):
            for x, value in zip((45, 175), values, strict=True):
                page.insert_text((x, 170 + 30 * n), value, fontname="FieldNotes")
        original = pdf.tobytes()
    with TestClient(app) as client:
        resp = client.post(
            "/api/documents",
            files={"file": ("учёт.pdf", original)},
            data={"title": "Маршрутный учёт", "year": 2020},
        )
        assert resp.status_code == 201
        doc_id = resp.json()["id"]
        items = client.get(
            "/api/observations", params={"document_id": doc_id, "taxon_id": 16}
        ).json()["items"]
        (obs,) = items
        detail = client.get(f"/api/observations/{obs['id']}").json()
        chunk = detail["chunk"]
        assert detail["document"]["status"] == "done"
        assert chunk["locator"]["pdf_page"] == 1 and obs["value_num"] == 6
        assert [line["text"] for line in chunk["lines"]] == ["Лось", "6"]
        for line, box in zip(chunk["lines"], obs["bboxes"], strict=True):
            assert chunk["text"][line["start"] : line["end"]] == line["text"]
            assert line["bbox"] == pytest.approx(box, abs=0.0001)
            x0, y0, x1, y1 = box
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert client.get(f"/api/documents/{doc_id}/file").content == original
