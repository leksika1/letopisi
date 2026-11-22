"""Exercise the user-visible upload-to-search workflow with a text document."""

import os
import time
from io import BytesIO
from uuid import uuid4

import httpx
from docx import Document


def test_uploaded_document_becomes_searchable(api: httpx.Client) -> None:
    book = Document()
    book.add_paragraph("15 июня 2020 года на реке Паз наблюдали двух лосей.")
    table = book.add_table(rows=2, cols=3)
    for row, cells in zip(
        table.rows,
        [
            ("Вид", "Дата наблюдения", "Количество"),
            ("Лось", "15.06.2020", "2"),
        ],
        strict=True,
    ):
        for cell, value in zip(row.cells, cells, strict=True):
            cell.text = value
    body = BytesIO()
    book.save(body)
    name = f"Наблюдения {uuid4().hex}"
    response = api.post(
        "/api/documents",
        files={"file": (f"{name}.docx", body.getvalue())},
        data={"title": name, "year": 2020},
    )
    assert response.status_code == 201
    doc_id = response.json()["id"]
    deadline = time.monotonic() + float(os.getenv("E2E_PROCESSING_TIMEOUT", "15"))
    while True:
        docs = api.get("/api/documents")
        docs.raise_for_status()
        document = next(doc for doc in docs.json() if doc["id"] == doc_id)
        found = api.get("/api/observations", params={"document_id": doc_id})
        found.raise_for_status()
        result = found.json()
        if document["status"] == "done" and result["total"] > 0:
            break
        assert (
            document["status"] not in {"failed", "unsupported"}
            and time.monotonic() < deadline
        ), (
            f"Uploaded document {doc_id}: status={document['status']}, searchable observations={result['total']}"
        )
        time.sleep(0.25)
    record = api.get(f"/api/observations/{result['items'][0]['id']}")
    record.raise_for_status()
    detail = record.json()
    assert detail["document"]["id"] == doc_id
    assert detail["observation"]["quote"] in detail["chunk"]["text"]
