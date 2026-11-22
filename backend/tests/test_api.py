import csv
import json
from collections.abc import Iterator
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock

import pymupdf
import pytest
import yaml
from docx import Document
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from openpyxl import Workbook, load_workbook
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from sqlalchemy import Engine

from backend.app.db import ROOT
from backend.app.main import app
from pipeline.ingest import jobs
from pipeline.load import load_fixtures

SPEC = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
SCHEMA = json.loads(
    (ROOT / "contracts/observation.schema.json").read_text(encoding="utf-8")
)
URI = "https://letopisi.local/contracts/openapi.yaml"
REGISTRY = Registry().with_resources(
    [(URI, Resource(SPEC, DRAFT202012)), (SCHEMA["$id"], Resource(SCHEMA, DRAFT202012))]
)


def check(response, path: str, method: str, status: int) -> None:
    assert response.status_code == status, response.text[:350]
    spec = SPEC["paths"][path][method]["responses"][str(status)]
    if "$ref" in spec:
        spec = SPEC["components"]["responses"][spec["$ref"].split("/")[-1]]
    content = spec.get("content", {})
    if not content:
        assert response.content == b""
        return
    mime = response.headers["content-type"].split(";")[0]
    assert mime in content
    if mime == "application/json":
        pointer = (
            "#/paths/"
            + path.replace("~", "~0").replace("/", "~1")
            + "/"
            + method
            + "/responses/"
            + str(status)
        )
        if "$ref" in SPEC["paths"][path][method]["responses"][str(status)]:
            pointer = SPEC["paths"][path][method]["responses"][str(status)]["$ref"]
        validator = Draft202012Validator(
            {"$ref": URI + pointer + "/content/application~1json/schema"},
            registry=REGISTRY,
            format_checker=FormatChecker(),
        )
        validator.validate(response.json())


@pytest.fixture
def client(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    load_fixtures(database)
    with TestClient(app) as http:
        yield http


def test_documents(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    check(client.get("/api/documents"), "/documents", "get", 200)
    with pymupdf.open() as pdf:
        pdf.new_page(width=72, height=144).insert_text(
            (3, 20), "A sample page, including enough characters to detect text."
        )
        data = pdf.tobytes()
    job = Mock()
    monkeypatch.setattr(jobs, "run", job)
    args = {
        "files": {"file": ("test.pdf", data, "application/pdf")},
        "data": {"year": "2020", "title": "Книга"},
    }
    res = client.post("/api/documents", **args)
    check(res, "/documents", "post", 201)
    doc = res.json()
    assert doc["status"] == "queued" and doc["pages"] == 1
    job.assert_called_once_with(doc["id"])
    url = f"/api/documents/{doc['id']}"
    raw = client.get(url + "/file")
    check(raw, "/documents/{id}/file", "get", 200)
    assert raw.content == data
    img = client.get(url + "/pages/1.png", params={"dpi": 100})
    check(img, "/documents/{id}/pages/{n}.png", "get", 200)
    pix = pymupdf.Pixmap(img.content)
    assert (pix.width, pix.height) == (100, 200)
    check(client.get(url + "/pages/2.png"), "/documents/{id}/pages/{n}.png", "get", 404)
    check(client.get("/api/documents/999999/file"), "/documents/{id}/file", "get", 404)
    check(client.post("/api/documents", **args), "/documents", "post", 409)
    check(
        client.post(
            "/api/documents", files={"file": ("x.exe", b"abc")}, data=args["data"]
        ),
        "/documents",
        "post",
        415,
    )
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
    check(client.post("/api/documents", **args), "/documents", "post", 413)
    check(
        client.post(
            "/api/documents", files=args["files"], data={"title": "X", "year": "3000"}
        ),
        "/documents",
        "post",
        422,
    )


@pytest.mark.parametrize("ext", ["docx", "xlsx"])
def test_office_original(client: TestClient, ext: str) -> None:
    stream = BytesIO()
    if ext == "docx":
        document = Document()
        document.add_paragraph("Лось у реки")
        document.save(stream)
    else:
        workbook = Workbook()
        workbook.active.append(["Вид", "Лось"])
        workbook.save(stream)
    original = stream.getvalue()
    res = client.post(
        "/api/documents",
        files={"file": (f"наблюдения.{ext}", original)},
        data={"title": "Учет", "year": "2020"},
    )
    check(res, "/documents", "post", 201)
    doc = res.json()
    assert doc["format"] == ext and doc["pages"] is None and doc["status"] == "queued"
    url = f"/api/documents/{doc['id']}"
    assert client.get(url + "/file").content == original
    check(client.get(url + "/pages/1.png"), "/documents/{id}/pages/{n}.png", "get", 404)


def test_edit_history_and_rollback(client: TestClient) -> None:
    url = "/api/observations/1"
    shape = "/observations/{id}"
    before = client.get(url)
    check(before, shape, "get", 200)
    assert before.json()["chunk"]["id"] == before.json()["observation"]["chunk_id"]
    initial = len(before.json()["edits"])
    fields = {"note": "сверено по книге", "person_ids": [2, 1]}
    res = client.patch(url, json={"editor": "Тест", "action": "edit", "fields": fields})
    check(res, shape, "patch", 200)
    value = res.json()
    assert value["observation"]["person_ids"] == [2, 1]
    assert len(value["edits"]) == initial + 1 and set(
        value["edits"][-1]["changes"]
    ) == set(fields)
    assert (
        value["edits"][-1]["changes"]["note"]["old"]
        == before.json()["observation"]["note"]
    )
    for action, status in [("verify", "verified"), ("reject", "rejected")]:
        result = client.patch(url, json={"editor": "Тест", "action": action})
        check(result, shape, "patch", 200)
        assert result.json()["observation"]["status"] == status
    for bad in (
        {"quote": "нельзя менять"},
        {"taxon_id": 99999999},
        {"person_ids": [1, 999999]},
    ):
        check(
            client.patch(url, json={"editor": "Тест", "action": "edit", "fields": bad}),
            shape,
            "patch",
            422,
        )
    assert client.get(url).json() == result.json()
    check(client.get("/api/observations/999999"), shape, "get", 404)


def test_saved_queries(client: TestClient) -> None:
    url = "/api/saved-queries"
    check(client.get(url), "/saved-queries", "get", 200)
    res = client.post(
        url, json={"name": "Лоси", "filters": {"years": [2020], "taxon": "Лось"}}
    )
    check(res, "/saved-queries", "post", 201)
    item = res.json()
    assert item in client.get(url).json()
    check(
        client.post(url, json={"name": "X", "filters": {"years": "2020"}}),
        "/saved-queries",
        "post",
        422,
    )
    check(client.delete(f"{url}/{item['id']}"), "/saved-queries/{id}", "delete", 204)
    check(client.delete(f"{url}/{item['id']}"), "/saved-queries/{id}", "delete", 404)


def test_exports(client: TestClient) -> None:
    cols = json.loads(
        (ROOT / "contracts/export_columns.json").read_text(encoding="utf-8")
    )
    expected = [c["header"] for c in cols]
    url = "/api/export"
    filters = {"taxon_id": 13, "years": "2020", "doc_years": "2020"}
    res = client.get(url, params={"format": "csv", **filters})
    check(res, "/export", "get", 200)
    assert res.content.startswith(b"\xef\xbb\xbf")
    records = list(csv.reader(StringIO(res.content.decode("utf-8-sig")), delimiter=";"))
    assert records[0] == expected and len(records) > 1
    listed = client.get("/api/observations", params=filters)
    assert listed.status_code == 200
    assert [
        int(row[13].split("/observations/")[1].split("?")[0]) for row in records[1:]
    ] == [item["id"] for item in listed.json()["items"]]
    assert all(
        row[1] == "2020" and row[4] == "Geranium sylvaticum" for row in records[1:]
    )
    excel = client.get(url, params={"format": "xlsx", **filters})
    check(excel, "/export", "get", 200)
    sheet = load_workbook(BytesIO(excel.content)).active
    assert [cell.value for cell in sheet[1]] == expected
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref == sheet.dimensions
    assert (
        sheet.max_row == len(records) and sheet["N2"].hyperlink.target == records[1][13]
    )
    assert "Герань" in sheet["D2"].value
    check(
        client.get(url, params={"format": "csv", "years": "abc"}), "/export", "get", 400
    )
    client.patch("/api/observations/1", json={"action": "reject", "editor": "Тест"})
    assert "/observations/1?" not in client.get(url, params={"format": "csv"}).text
    assert (
        "/observations/1?"
        in client.get(url, params={"format": "csv", "status": "rejected"}).text
    )
