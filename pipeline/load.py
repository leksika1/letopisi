"""Seed PostgreSQL from the checked-in contract fixtures."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

if __package__ in (None, ""):
    # Direct script execution puts pipeline/, not the repository, on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import ROOT, database_engine

FIXTURES = ROOT / "contracts" / "fixtures"
# File names and table identifiers are fixed by the contract, in FK order.
TABLE_FILES = (
    ("documents", "documents.json"),
    ("sections", "sections.json"),
    ("chunks", "chunks.jsonl"),
    ("taxa", "taxa.json"),
    ("taxon_names", "taxon_names.json"),
    ("places", "places.json"),
    ("place_names", "place_names.json"),
    ("persons", "persons.json"),
    ("phenophases", "phenophases.json"),
    ("observations", "observations.jsonl"),
    ("observation_edits", "observation_edits.jsonl"),
    ("saved_queries", "saved_queries.json"),
)


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in stream if line.strip()]
        rows = json.load(stream)
    if not isinstance(rows, list):
        raise TypeError(f"{path}: expected an array")
    return rows


def seed_table(conn: Connection, table: str, rows: list[dict[str, Any]]) -> set[int]:
    # PostgreSQL uses the contract's column types for dates, arrays and JSONB.
    query = text(
        f"INSERT INTO {table} "
        f"SELECT * FROM jsonb_populate_recordset(NULL::{table}, CAST(:rows AS jsonb)) "
        "ON CONFLICT (id) DO NOTHING RETURNING id"
    )
    return set(conn.execute(query, {"rows": json.dumps(rows)}).scalars())


def load_fixtures(engine: Engine, directory: Path = FIXTURES) -> dict[str, int]:
    inserted: dict[str, int] = {}
    with engine.begin() as conn:
        # Ordinary writers and simultaneous imports must agree on the next identity.
        tables = ", ".join(table for table, _ in TABLE_FILES)
        conn.exec_driver_sql(
            f"LOCK TABLE {tables}, observation_persons IN SHARE ROW EXCLUSIVE MODE"
        )
        for table, filename in TABLE_FILES:
            rows = read_rows(directory / filename)
            if table == "sections":
                for row in rows:
                    row.setdefault("author_person_ids", [])
            ids = seed_table(conn, table, rows)
            inserted[table] = len(ids)
            if table == "observations":
                links = [
                    {"obs": row["id"], "person": person, "ord": index}
                    for row in rows
                    if row["id"] in ids
                    for index, person in enumerate(row["person_ids"])
                ]
                if links:
                    conn.execute(
                        text(
                            "INSERT INTO observation_persons (observation_id, person_id, ord) "
                            "VALUES (:obs, :person, :ord)"
                        ),
                        links,
                    )
                inserted["observation_persons"] = len(links)

        # Explicit fixture IDs bypass identity defaults. Never rewind a used sequence.
        for table, _ in TABLE_FILES:
            conn.execute(
                text(
                    f"WITH state AS (SELECT max(id) AS top_id, "
                    f"pg_get_serial_sequence('{table}', 'id')::regclass AS seq FROM {table}) "
                    "SELECT setval(seq, top_id) FROM state "
                    "WHERE top_id > coalesce(pg_sequence_last_value(seq), 0)"
                )
            )
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=FIXTURES)
    engine = database_engine()
    try:
        counts = load_fixtures(engine, parser.parse_args().directory)
    finally:
        engine.dispose()
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
