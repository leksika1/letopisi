from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from backend.app.db import apply_schema, database_engine


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Run each test in a new database on the configured PostgreSQL instance."""
    admin = database_engine()
    name = f"letopisi_test_{uuid4().hex}"
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
            try:
                url = admin.url.set(database=name)
                monkeypatch.setenv(
                    "DATABASE_URL", url.render_as_string(hide_password=False)
                )
                engine = database_engine()
                try:
                    apply_schema(engine)
                    yield engine
                finally:
                    engine.dispose()
            finally:
                conn.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        admin.dispose()
