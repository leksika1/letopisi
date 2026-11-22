"""Seed PostgreSQL from the checked-in contract fixtures."""

import argparse
import csv
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
DICTIONARY_TABLES = (
    "taxa",
    "taxon_names",
    "places",
    "place_names",
    "persons",
    "phenophases",
)
CSV_DICTIONARIES = (
    (
        "taxa",
        ("name_lat",),
        (
            "name_ru",
            "rank",
            "genus",
            "family",
            '"order"',
            "class",
            "group_ru",
            "gbif_key",
        ),
    ),
    ("taxon_names", ("taxon_id", "name"), ("kind", "lang")),
    ("places", ("name",), ("lat", "lon", "coords_source")),
    ("place_names", ("place_id", "name"), ("kind",)),
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


def check_document_sources(conn: Connection, rows: list[dict[str, Any]]) -> None:
    # Child fixtures refer to fixed IDs. Skipping an unrelated upload with the
    # same ID would silently attach the book's observations to that upload.
    conflict = (
        conn.execute(
            text("""
            SELECT f.id AS fixture_id, f.filename AS fixture_file,
                   d.id AS existing_id, d.filename AS existing_file
            FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                AS f(id bigint, filename text, format text, year integer, sha256 text)
            JOIN documents d ON d.id = f.id OR d.sha256 = f.sha256
            WHERE d.id <> f.id OR CASE
                WHEN d.sha256 IS NOT NULL AND f.sha256 IS NOT NULL THEN d.sha256 <> f.sha256
                ELSE (d.filename, d.format, d.year) IS DISTINCT FROM (f.filename, f.format, f.year)
            END
            ORDER BY f.id, d.id LIMIT 1
        """),
            {"rows": json.dumps(rows)},
        )
        .mappings()
        .first()
    )
    if conflict:
        raise ValueError(
            f"Конфликт источников: документ фикстуры {conflict['fixture_id']} "
            f"({conflict['fixture_file']}) и документ базы {conflict['existing_id']} "
            f"({conflict['existing_file']})."
        )


def enrich_dictionaries(conn: Connection, directory: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, identity, columns in CSV_DICTIONARIES:
        path = directory / f"{table}.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = [
                {k: v or None for k, v in row.items()} for row in csv.DictReader(stream)
            ]
        params = {"rows": json.dumps(rows)}
        records = f"jsonb_populate_recordset(NULL::{table}, CAST(:rows AS jsonb))"
        different = " OR ".join(f"t.{key} IS DISTINCT FROM f.{key}" for key in identity)
        conflict = conn.scalar(
            text(
                f"SELECT t.id FROM {table} t JOIN {records} f USING (id) WHERE {different} LIMIT 1"
            ),
            params,
        )
        if conflict is not None:
            raise ValueError(
                f"{path.name}: ID {conflict} уже принадлежит другой записи"
            )
        inserted = seed_table(conn, table, rows)
        # Empty enrichment cells carry no new information (e.g. an ungeocoded place).
        assignments = ", ".join(f"{c} = COALESCE(f.{c}, t.{c})" for c in columns)
        changes = " OR ".join(
            f"(f.{c} IS NOT NULL AND f.{c} IS DISTINCT FROM t.{c})" for c in columns
        )
        updated = conn.execute(
            text(
                f"UPDATE {table} t SET {assignments} FROM {records} f "
                f"WHERE t.id = f.id AND ({changes}) RETURNING t.id"
            ),
            params,
        )
        counts[table] = len(inserted) + len(updated.all())
    return counts


def sync_identity(conn: Connection, table: str) -> None:
    # Explicit fixture IDs bypass identity defaults. Never rewind a used sequence.
    conn.execute(
        text(
            f"WITH state AS (SELECT max(id) AS top_id, "
            f"pg_get_serial_sequence('{table}', 'id')::regclass AS seq FROM {table}) "
            "SELECT setval(seq, top_id) FROM state "
            "WHERE top_id > coalesce(pg_sequence_last_value(seq), 0)"
        )
    )


def load_dictionaries(conn: Connection) -> dict[str, int]:
    """Install reference data for extraction without importing any demonstration books."""
    conn.exec_driver_sql(
        f"LOCK TABLE {', '.join(DICTIONARY_TABLES)} IN SHARE ROW EXCLUSIVE MODE"
    )
    counts = {
        table: len(seed_table(conn, table, read_rows(FIXTURES / f"{table}.json")))
        for table in DICTIONARY_TABLES
    }
    for table, changed in enrich_dictionaries(conn, ROOT / "dictionaries").items():
        counts[table] += changed
    for table in DICTIONARY_TABLES:
        sync_identity(conn, table)
    return counts


def load_fixtures(
    engine: Engine, directory: Path = FIXTURES, *, dictionaries: bool | None = None
) -> dict[str, int]:
    inserted: dict[str, int] = {}
    with engine.begin() as conn:
        # Ordinary writers and simultaneous imports must agree on the next identity.
        tables = ", ".join(table for table, _ in TABLE_FILES)
        conn.exec_driver_sql(
            f"LOCK TABLE {tables}, observation_persons IN SHARE ROW EXCLUSIVE MODE"
        )
        for table, filename in TABLE_FILES:
            rows = read_rows(directory / filename)
            if table == "documents":
                check_document_sources(conn, rows)
            if table == "sections":
                for row in rows:
                    row.setdefault("author_person_ids", [])
            if table == "observations" and (
                dictionaries
                if dictionaries is not None
                else directory.resolve() != FIXTURES.resolve()
            ):
                inserted["dictionary_changes"] = sum(
                    enrich_dictionaries(conn, ROOT / "dictionaries").values()
                )
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

        for table, _ in TABLE_FILES:
            sync_identity(conn, table)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=FIXTURES)
    parser.add_argument(
        "--dictionaries",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Apply enriched CSV dictionaries (default for release bundles).",
    )
    args = parser.parse_args()
    engine = database_engine()
    try:
        counts = load_fixtures(engine, args.directory, dictionaries=args.dictionaries)
    finally:
        engine.dispose()
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
