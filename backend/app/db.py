"""PostgreSQL connection settings and the initial contract migration."""

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Connection, Engine, create_engine, make_url, text

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DATABASE_URL = "postgresql+psycopg://letopisi:letopisi@127.0.0.1:5432/letopisi"


def database_engine() -> Engine:
    url = make_url(os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def get_conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.connect() as conn:
        yield conn


def apply_schema(engine: Engine) -> None:
    sql = (ROOT / "contracts" / "schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        # Serialize startup DDL when multiple workers share a database.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('letopisi.schema'))"))
        conn.exec_driver_sql(sql, execution_options={"no_parameters": True})
        # Upgrade volumes created with contract v1.0; CREATE IF NOT EXISTS skips them.
        conn.exec_driver_sql(
            "ALTER TABLE sections ADD COLUMN IF NOT EXISTS authors_raw text, "
            "ADD COLUMN IF NOT EXISTS author_person_ids bigint[] NOT NULL DEFAULT '{}'"
        )
        conn.exec_driver_sql(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS lines jsonb "
            "CHECK (lines IS NULL OR jsonb_typeof(lines)='array')"
        )
