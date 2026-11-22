"""Cross-component failures, conflicting imports and concurrent writes through nginx."""

import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import httpx
import pymupdf
import pytest
from sqlalchemy import create_engine, make_url, text

from backend.app.db import LOCAL_DATABASE_URL, ROOT
from infra.e2e.conftest import backend, gateway, temporary_database
from infra.e2e.test_api_e2e import ContractAPI


@pytest.mark.parametrize(
    "fields",
    [
        {"date_to": "2020-06-20"},
        {"date_precision": None},
        {"obs_year": 1997},
        {"taxon_id": None},
        {"flags": ["quote_not_found"]},
        {"document_id": 2},
        {"quote": "замена исходной цитаты"},
        {"person_ids": [999999999]},
    ],
)
def test_failed_edit_leaves_source_fields_and_history_intact(
    api: httpx.Client, fields: dict[str, Any]
) -> None:
    service = ContractAPI(api)
    before = service.call("getObservation", path={"id": 1}).json()
    service.call(
        "patchObservation",
        422,
        path={"id": 1},
        body={"action": "edit", "editor": "E2E", "fields": fields},
    )
    assert service.call("getObservation", path={"id": 1}).json() == before


def test_concurrent_edits_produce_a_continuous_history(api: httpx.Client) -> None:
    url = "/api/observations/6"
    before = api.get(url).json()
    original = before["observation"]["note"]
    notes = [uuid4().hex, uuid4().hex]
    barrier = Barrier(2)

    def update(note: str) -> None:
        barrier.wait(timeout=10)
        response = api.patch(
            url, json={"action": "edit", "editor": "E2E", "fields": {"note": note}}
        )
        response.raise_for_status()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(update, notes))
        after = api.get(url).json()
        new = after["edits"][len(before["edits"]) :]
        assert len(new) == 2
        first, last = [entry["changes"]["note"] for entry in new]
        assert first["old"] == original
        assert first["new"] == last["old"]
        assert last["new"] == after["observation"]["note"]
        assert {first["new"], last["new"]} == set(notes)
    finally:
        api.patch(
            url, json={"action": "edit", "editor": "E2E", "fields": {"note": original}}
        ).raise_for_status()


def test_duplicate_upload_race_then_seed_conflict(
    pipeline_service: tuple[httpx.Client, str, Path],
) -> None:
    api, database, _ = pipeline_service
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text(
            (20, 40),
            "An unrelated uploaded file already occupies the first document ID.",
        )
        data = pdf.tobytes()
    barrier = Barrier(2)

    def upload(filename: str) -> httpx.Response:
        barrier.wait(timeout=10)
        return api.post(
            "/api/documents",
            files={"file": (filename, data, "application/pdf")},
            data={"title": "Полевой журнал", "year": 2020},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, ["first.pdf", "second.pdf"]))
    assert sorted(r.status_code for r in responses) == [201, 409]
    (doc,) = api.get("/api/documents").json()
    assert api.get(f"/api/documents/{doc['id']}/file").content == data
    seed = subprocess.run(
        [sys.executable, "pipeline/load.py"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert seed.returncode != 0 and doc["filename"] in seed.stderr
    assert api.get("/api/documents").json() == [doc]
    assert api.get("/api/observations").json()["total"] == 0


@contextmanager
def disconnect_disposable_db(database_url: str) -> Iterator[None]:
    name = make_url(database_url).database
    assert (
        name
        and name.startswith("letopisi_e2e_")
        and len(name) == len("letopisi_e2e_") + 32
    )
    admin_url = make_url(os.getenv("TEST_DATABASE_URL") or LOCAL_DATABASE_URL)
    if admin_url.drivername == "postgresql":
        admin_url = admin_url.set(drivername="postgresql+psycopg")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            quoted = engine.dialect.identifier_preparer.quote_identifier(name)
            conn.exec_driver_sql(f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS false")
            try:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db"
                    ),
                    {"db": name},
                )
                yield
            finally:
                conn.exec_driver_sql(f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS true")
    finally:
        engine.dispose()


def test_health_tracks_database_outage_and_recovery(
    pipeline_service: tuple[httpx.Client, str, Path],
) -> None:
    api, database, _ = pipeline_service
    service = ContractAPI(api)
    with disconnect_disposable_db(database):
        degraded = service.call("getHealth").json()
        assert degraded["db"] is False and degraded["status"] == "degraded"
    healthy = service.call("getHealth").json()
    assert healthy["db"] is True and healthy["status"] == "ok"


def test_restart_upgrades_a_populated_v1_volume(tmp_path: Path) -> None:
    with temporary_database() as url:
        first = tmp_path / "first"
        first.mkdir()
        with backend(url, first, seed=True) as service:
            note = {
                "action": "edit",
                "editor": "E2E",
                "fields": {"note": "Проверено до обновления"},
            }
            service.patch("/api/observations/1", json=note).raise_for_status()
            before = service.get("/api/observations/1").json()
            total = service.get("/api/observations").json()["total"]

        # v1.1 added exactly these columns to the published v1.0 schema.
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE sections DROP COLUMN authors_raw, DROP COLUMN author_person_ids"
                )
                conn.exec_driver_sql("ALTER TABLE chunks DROP COLUMN lines")
        finally:
            engine.dispose()

        second = tmp_path / "upgraded"
        second.mkdir()
        with (
            backend(url, second, seed=False) as service,
            gateway(service, second / "nginx") as api,
        ):
            contract = ContractAPI(api)
            after = contract.call("getObservation", path={"id": 1}).json()
            assert after["observation"] == before["observation"]
            assert (
                after["edits"] == before["edits"]
                and after["edits"][-1]["editor"] == "E2E"
            )
            assert after["chunk"]["text"] == before["chunk"]["text"]
            assert after["chunk"]["lines"] is None
            assert after["section"]["authors_raw"] is None
            assert after["section"]["author_person_ids"] == []
            assert contract.call("listObservations").json()["total"] == total
