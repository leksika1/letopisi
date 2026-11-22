"""Public API workflows against the running service and the contract fixture database."""

import csv
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pymupdf
import pytest
import yaml
from docx import Document
from jsonschema import Draft202012Validator, FormatChecker
from openpyxl import Workbook, load_workbook
from referencing import Registry
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SPEC = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
SCHEMA = json.loads((CONTRACTS / "observation.schema.json").read_text(encoding="utf-8"))
SPEC_URI = "https://letopisi.local/contracts/openapi.yaml"
REGISTRY = Registry().with_resources(
    [
        (SPEC_URI, DRAFT202012.create_resource(SPEC)),
        (SCHEMA["$id"], DRAFT202012.create_resource(SCHEMA)),
    ]
)
OPERATIONS = {
    operation["operationId"]: (method, path, operation["responses"])
    for path, item in SPEC["paths"].items()
    for method, operation in item.items()
    if method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ContractAPI:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.covered: set[str] = set()

    def call(
        self,
        operation_id: str,
        status: int = 200,
        *,
        path: Mapping[str, str | int] | None = None,
        params: Mapping[str, str | int] | None = None,
        body: Mapping[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        method, template, responses = OPERATIONS[operation_id]
        target = "/api" + template.format_map(path or {})
        response = self.client.request(
            method.upper(), target, params=params, json=body, data=data, files=files
        )
        assert (response.request.method, response.request.url.path) == (
            method.upper(),
            target,
        )
        assert response.status_code == status, (
            operation_id,
            response.status_code,
            response.text[:500],
        )
        response_schema = responses[str(status)]
        pointer = response_schema.get("$ref")
        if pointer:
            response_schema = SPEC["components"]["responses"][pointer.rsplit("/", 1)[1]]
        else:
            escaped = template.replace("~", "~0").replace("/", "~1")
            pointer = f"#/paths/{escaped}/{method}/responses/{status}"
        if "content" in response_schema:
            mime = response.headers["content-type"].split(";", 1)[0]
            assert mime in response_schema["content"], (operation_id, mime)
            if mime == "application/json":
                validator = Draft202012Validator(
                    {"$ref": SPEC_URI + pointer + "/content/application~1json/schema"},
                    registry=REGISTRY,
                    format_checker=FormatChecker(),
                )
                validator.validate(response.json())
            else:
                assert response.content
        else:
            assert response.content == b""
        if status < 300:
            self.covered.add(operation_id)
        return response


def search_and_analytics(service: ContractAPI) -> None:
    text = "Все наблюдения за лосем в 1995, 1996 и 2022 годах по хронологии"
    parsed = service.call("parseQuery", body={"text": text}).json()
    filters = parsed["filters"]
    assert filters["taxon_id"] == 16 and filters["years"] == [1995, 1996, 2020]
    assert (filters["sort"], filters["order"]) == ("date_from", "asc")
    assert any(
        "2022" in warning and "2020" in warning for warning in parsed["warnings"]
    )
    query = {
        key: ",".join(map(str, value)) if isinstance(value, list) else value
        for key, value in filters.items()
    }
    found = service.call("listObservations", params=query).json()
    assert found["total"] == len(found["items"]) > 1
    assert all(
        r["taxon_id"] == 16 and r["obs_year"] in filters["years"]
        for r in found["items"]
    )
    first_ids = []
    for page in (1, 2):
        single = service.call(
            "listObservations", params={**query, "page": page, "page_size": 1}
        ).json()
        assert (single["total"], single["page"], single["page_size"]) == (
            found["total"],
            page,
            1,
        )
        first_ids.append(single["items"][0]["id"])
    assert first_ids == [r["id"] for r in found["items"][:2]]
    assert first_ids[0] != first_ids[1]

    for kind, name, item_id in (
        ("taxa", "Герань лесная", 13),
        ("places", "Калкупя", 4),
        ("persons", "Макарова", 1),
        ("phenophases", "зацветание", 5),
    ):
        options = service.call(
            "suggest", path={"kind": kind}, params={"q": name, "limit": 5}
        ).json()
        assert 0 < len(options) <= 5 and item_id in [o["id"] for o in options]

    phenology = service.call(
        "getPhenology",
        params={"taxon_id": 13, "phenophase_id": 5, "years": "2018,2019"},
    ).json()
    assert [r["year"] for r in phenology] == [2018, 2019]
    for row in phenology:
        detail = service.call(
            "getObservation", path={"id": row["observation_id"]}
        ).json()
        obs = detail["observation"]
        assert (
            obs["taxon_id"],
            obs["phenophase_id"],
            obs["obs_year"],
            obs["date_from"],
        ) == (row["taxon_id"], row["phenophase_id"], row["year"], row["date_from"])
        assert obs["document_id"] == detail["document"]["id"]
        assert (
            obs["chunk_id"] == detail["chunk"]["id"]
            and obs["section_id"] == detail["section"]["id"]
        )
        start, end = obs["quote_span"]
        assert detail["chunk"]["text"][start:end] == obs["quote"]
    species = service.call(
        "getPlaceSpecies", path={"id": 4}, params={"year": 1995}
    ).json()
    assert species["place"]["id"] == 4
    hare = next(r for r in species["items"] if r["taxon_id"] == 18)
    records = service.call(
        "listObservations", params={"place_id": 4, "taxon_id": 18, "years": "1995"}
    ).json()
    assert hare["n_records"] == records["total"] == 3

    service.call("parseQuery", 422, body={"text": ""})
    service.call("listObservations", 422, params={"years": "abc"})
    service.call("suggest", 400, path={"kind": "unknown"}, params={"q": "тест"})
    service.call("getPhenology", 404, params={"taxon_id": 999999999})
    service.call("getPlaceSpecies", 404, path={"id": 999999999})
    service.call("getObservation", 404, path={"id": 999999999})


def saved_queries(service: ContractAPI) -> None:
    label = f"Лось E2E {uuid4().hex}"
    body = {"name": label, "text": "Лось", "filters": {"taxon_id": 16, "years": [1995]}}
    created = service.call("createSavedQuery", 201, body=body).json()
    try:
        assert all(created[k] == value for k, value in body.items())
        assert created in service.call("listSavedQueries").json()
        service.call(
            "createSavedQuery", 422, body={"name": label, "filters": {"years": "1995"}}
        )
    finally:
        service.call("deleteSavedQuery", 204, path={"id": created["id"]})
    assert created["id"] not in [
        q["id"] for q in service.call("listSavedQueries").json()
    ]
    service.call("deleteSavedQuery", 404, path={"id": created["id"]})


def observation_edits_and_exports(service: ContractAPI) -> None:
    service.call(
        "patchObservation",
        404,
        path={"id": 999999999},
        body={"action": "verify", "editor": "E2E"},
    )
    path = {"id": 6}
    before = service.call("getObservation", path=path).json()
    assert before["observation"]["status"] == "edited"
    note = f"Проверено E2E {uuid4().hex}"
    payload = {"editor": "E2E", "action": "edit", "fields": {"note": note}}
    changed = service.call("patchObservation", path=path, body=payload).json()
    try:
        assert len(changed["edits"]) == len(before["edits"]) + 1
        assert changed["edits"][-1]["changes"]["note"] == {
            "old": before["observation"]["note"],
            "new": note,
        }
        assert (
            service.call("getObservation", path=path).json()["observation"]["note"]
            == note
        )
        service.call(
            "patchObservation",
            422,
            path=path,
            body={**payload, "fields": {"taxon_id": 999999999}},
        )
        assert service.call("getObservation", path=path).json() == changed
        verified = service.call(
            "patchObservation", path=path, body={"editor": "E2E", "action": "verify"}
        ).json()
        assert verified["observation"]["status"] == "verified"
        filters: dict[str, str | int] = {
            "taxon_id": 13,
            "years": "2020",
            "doc_years": "2020",
            "sort": "id",
        }
        listing = service.call("listObservations", params=filters).json()
        headers = [
            c["header"]
            for c in json.loads(
                (CONTRACTS / "export_columns.json").read_text(encoding="utf-8")
            )
        ]
        csv_file = service.call(
            "exportObservations", params={**filters, "format": "csv"}
        )
        assert re.fullmatch(
            r'attachment; filename="letopisi_export_\d{8}_\d{6}\.csv"',
            csv_file.headers["content-disposition"],
        )
        assert csv_file.content.startswith(b"\xef\xbb\xbf")
        csv_rows = list(
            csv.reader(StringIO(csv_file.content.decode("utf-8-sig")), delimiter=";")
        )
        assert csv_rows[0] == headers
        assert len(csv_rows) == listing["total"] + 1
        excel = service.call("exportObservations", params={**filters, "format": "xlsx"})
        assert excel.headers["content-type"] == XLSX
        book = load_workbook(BytesIO(excel.content))
        sheet = book.active
        assert [cell.value for cell in sheet[1]] == headers
        assert sheet.max_row == len(csv_rows) and sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == sheet.dimensions
        for i, (record, row) in enumerate(
            zip(listing["items"], csv_rows[1:], strict=True), 2
        ):
            expected = (
                f"/observations/{record['id']}?page={record['locator']['pdf_page']}"
            )
            url = urlsplit(row[13])
            assert url.scheme in {"http", "https"} and url.netloc
            assert f"{url.path}?{url.query}" == expected
            assert sheet.cell(i, 14).hyperlink.target == row[13]
            if record["id"] == path["id"]:
                assert row[12] == note and row[14].startswith("подтверждено")
                assert sheet.cell(i, 13).value == note
        book.close()

        rejected = service.call(
            "patchObservation", path=path, body={"editor": "E2E", "action": "reject"}
        ).json()
        assert rejected["observation"]["status"] == "rejected"
        default = service.call("listObservations", params=filters).json()
        assert default["total"] == listing["total"] - 1
        assert path["id"] not in [r["id"] for r in default["items"]]
        for extra, includes in (({}, False), ({"status": "rejected"}, True)):
            exported = service.call(
                "exportObservations", params={**filters, "format": "csv", **extra}
            )
            rows = list(
                csv.reader(
                    StringIO(exported.content.decode("utf-8-sig")), delimiter=";"
                )
            )
            assert (note in [row[12] for row in rows[1:]]) is includes
        service.call("exportObservations", 400, params={"format": "invalid"})
    finally:
        service.call(
            "patchObservation",
            path=path,
            body={**payload, "fields": {"note": before["observation"]["note"]}},
        )


def uploaded_documents(service: ContractAPI) -> None:
    label = f"E2E {uuid4().hex}"
    with pymupdf.open() as pdf:
        pdf.new_page(width=144, height=72).insert_text((4, 20), label, fontsize=5)
        pdf.new_page(width=72, height=144)
        pdf_data = pdf.tobytes()
    word = BytesIO()
    document = Document()
    document.add_paragraph(label)
    document.save(word)
    excel = BytesIO()
    book = Workbook()
    book.active.append(["Вид", label])
    book.save(excel)
    docs_before = {d["id"] for d in service.call("listDocuments").json()}
    uploaded_ids: set[int] = set()
    form = {"title": label, "year": "2020"}
    for ext, mime, content in (
        ("pdf", "application/pdf", pdf_data),
        ("docx", DOCX, word.getvalue()),
        ("xlsx", XLSX, excel.getvalue()),
    ):
        files = {"file": (f"наблюдения.{ext}", content, mime)}
        doc = service.call("uploadDocument", 201, data=form, files=files).json()
        uploaded_ids.add(doc["id"])
        assert doc["id"] not in docs_before and doc["status"] == "queued"
        assert (
            doc["format"] == ext
            and doc["sha256"] == hashlib.sha256(content).hexdigest()
        )
        path = {"id": doc["id"]}
        raw = service.call("getDocumentFile", path=path)
        assert raw.content == content and raw.headers["content-type"] == mime
        headers = Message()
        headers["Content-Disposition"] = raw.headers["content-disposition"]
        assert headers.get_content_disposition() == (
            "inline" if ext == "pdf" else "attachment"
        )
        assert headers.get_filename() == doc["filename"] == f"наблюдения.{ext}"
        if ext == "pdf":
            assert doc["pages"] == 2
            image = service.call(
                "getDocumentPage", path={**path, "n": 2}, params={"dpi": 100}
            )
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
            pix = pymupdf.Pixmap(image.content)
            assert (pix.width, pix.height) == (100, 200)
            service.call("getDocumentPage", 404, path={**path, "n": 3})
        else:
            assert doc["pages"] is None
            service.call("getDocumentPage", 404, path={**path, "n": 1})
        service.call("uploadDocument", 409, data=form, files=files)
    assert len(uploaded_ids) == 3
    assert docs_before | uploaded_ids <= {
        d["id"] for d in service.call("listDocuments").json()
    }
    service.call("getDocumentFile", 404, path={"id": 999999999})
    service.call(
        "uploadDocument",
        413,
        data=form,
        files={"file": ("over-limit.pdf", b"0" * (1024 * 1024 + 1), "application/pdf")},
    )
    service.call(
        "uploadDocument",
        415,
        data=form,
        files={"file": ("заметка.txt", b"text", "text/plain")},
    )
    service.call(
        "uploadDocument",
        422,
        data={**form, "year": "0"},
        files={"file": ("book.pdf", pdf_data, "application/pdf")},
    )


def health_check(service: ContractAPI) -> None:
    health = service.call("getHealth").json()
    assert health["status"] == "ok" and health["db"] is True


SCENARIOS: list[tuple[Callable[[ContractAPI], None], set[str]]] = [
    (health_check, {"getHealth"}),
    (
        search_and_analytics,
        {
            "parseQuery",
            "listObservations",
            "suggest",
            "getPhenology",
            "getObservation",
            "getPlaceSpecies",
        },
    ),
    (saved_queries, {"createSavedQuery", "listSavedQueries", "deleteSavedQuery"}),
    (
        observation_edits_and_exports,
        {
            "getObservation",
            "patchObservation",
            "listObservations",
            "exportObservations",
        },
    ),
    (
        uploaded_documents,
        {"listDocuments", "uploadDocument", "getDocumentFile", "getDocumentPage"},
    ),
]
QUALITY_OPERATIONS = {"getQuality"}


def test_workflows_cover_all_openapi_operations() -> None:
    assert QUALITY_OPERATIONS.union(*(expected for _, expected in SCENARIOS)) == set(
        OPERATIONS
    )


@pytest.mark.parametrize(
    "workflow,expected", SCENARIOS, ids=[f.__name__ for f, _ in SCENARIOS]
)
def test_public_api_workflow(
    api: httpx.Client, workflow: Callable[[ContractAPI], None], expected: set[str]
) -> None:
    service = ContractAPI(api)
    workflow(service)
    assert service.covered == expected


@pytest.mark.parametrize("document_id", [None, 3], ids=["overall", "document"])
def test_quality_report(api: httpx.Client, document_id: int | None) -> None:
    service = ContractAPI(api)
    params = {"document_id": document_id} if document_id is not None else None
    report = service.call("getQuality", params=params).json()
    if document_id is None:
        assert 3 in [row["document_id"] for row in report["by_document"]]
    else:
        assert [row["document_id"] for row in report["by_document"]] == [document_id]
        for key in ("error_examples", "unsupported"):
            assert all(row["document_id"] == document_id for row in report[key])
    assert service.covered == QUALITY_OPERATIONS
