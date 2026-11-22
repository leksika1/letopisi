import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.app.db import ROOT
from backend.app.main import app
from pipeline.load import FIXTURES, load_fixtures, read_rows


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_health(database: Engine) -> None:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        result = await client.get("/api/health")
        assert result.status_code == 200
        assert result.json() == {"status": "ok", "db": True, "version": "1.0.0"}
        with patch.object(
            app.state.engine,
            "connect",
            side_effect=OperationalError("", {}, Exception()),
        ):
            result = await client.get("/api/health")
            assert result.status_code == 200
            assert result.json() == {
                "status": "degraded",
                "db": False,
                "version": "1.0.0",
            }


def test_load_twice(database: Engine) -> None:
    expected = {
        path.stem: len(read_rows(path))
        for path in FIXTURES.glob("*.json*")
        if path.name != "quality.json"
    }
    observations = read_rows(FIXTURES / "observations.jsonl")
    links = sorted(
        (row["id"], i, person)
        for row in observations
        for i, person in enumerate(row["person_ids"])
    )
    expected["observation_persons"] = len(links)

    for run in range(2):
        result = subprocess.run(
            [sys.executable, "pipeline/load.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == (
            expected if run == 0 else dict.fromkeys(expected, 0)
        )
        with database.connect() as conn:
            counts = {
                table: conn.exec_driver_sql(
                    f"SELECT count(*) FROM {table}"
                ).scalar_one()
                for table in expected
            }
            assert counts == expected
            assert (
                conn.exec_driver_sql(
                    "SELECT observation_id, ord, person_id FROM observation_persons ORDER BY 1, 2"
                ).all()
                == links
            )

    with database.begin() as conn:
        assert conn.scalar(text("SELECT cardinality(show_trgm('лось'))")) > 0
        highest = conn.scalar(text("SELECT max(id) FROM observations"))
        assert (
            conn.scalar(
                text("SELECT nextval(pg_get_serial_sequence('observations', 'id'))")
            )
            > highest
        )


def test_invalid_fixture_rolls_back(database: Engine, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    path = fixtures / "observation_edits.jsonl"
    edits = read_rows(path)
    edits[0]["observation_id"] = -1
    path.write_text("\n".join(json.dumps(row) for row in edits), encoding="utf-8")

    with pytest.raises(IntegrityError):
        load_fixtures(database, fixtures)
    with database.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM documents")) == 0
        assert conn.scalar(text("SELECT count(*) FROM observations")) == 0


@pytest.mark.parametrize(
    "same_hash", [False, True], ids=["occupied-id", "hash-at-other-id"]
)
def test_seed_rejects_conflicting_document(database: Engine, same_hash: bool) -> None:
    source = read_rows(FIXTURES / "documents.json")[0]
    with database.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents(id,title,filename,format,sha256) "
                "VALUES (:id,'Upload','uploaded.pdf','pdf',:sha)"
            ),
            {
                "id": 100 if same_hash else source["id"],
                "sha": source["sha256"] if same_hash else "a" * 64,
            },
        )
    with pytest.raises(ValueError, match="uploaded.pdf"):
        load_fixtures(database)
    with database.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM documents")) == 1
        assert conn.scalar(text("SELECT count(*) FROM observations")) == 0
