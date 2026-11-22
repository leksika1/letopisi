"""Process uploaded documents and publish their extracted records atomically."""

import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from backend.app.db import ROOT, database_engine
from pipeline.load import load_dictionaries, read_rows, seed_table

log = logging.getLogger(__name__)


class ProcessingError(Exception):
    """An error suitable for the document status shown to the user."""


def extract(source: Path, data: Path, document: dict[str, Any]) -> None:
    # run() resolves IDs through a process-local, cached document catalog.
    # Only this child sees the upload; concurrent workers and API searches
    # keep their own catalog and configuration.
    from pipeline.ingest import ingest
    from pipeline.normalize.dicts import load
    from pipeline.run import run as extract_records

    load().documents = [document]
    doc_id = document["id"]
    stats = ingest(source, doc_id, data)
    if stats.skipped_ocr:
        raise ProcessingError(
            "Не выполнен OCR страниц: " + ", ".join(map(str, stats.skipped_ocr))
        )
    report = extract_records(str(doc_id), quiet=True)
    if report.get("llm_error") or report["llm_usage"]["errors"]:
        raise ProcessingError("Не удалось извлечь все наблюдения с помощью LLM.")
    if report["invalid_dropped"]:
        raise ProcessingError(
            "Часть извлечённых наблюдений не прошла проверку контракта."
        )


def assign_ids(
    conn: Connection, table: str, rows: list[dict[str, Any]]
) -> dict[int, int]:
    # In-memory IDs from the extractor are local to this import. Use PostgreSQL
    # identities in the service so a concurrent import cannot reuse another row.
    ids = conn.scalars(
        text(
            "SELECT nextval(pg_get_serial_sequence(:table, 'id')) FROM generate_series(1,:n)"
        ),
        {"table": table, "n": len(rows)},
    )
    mapping = {}
    for row, pk in zip(rows, ids, strict=True):
        mapping[row["id"]] = pk
        row["id"] = pk
    return mapping


def publish(conn: Connection, data: Path, doc_id: int) -> None:
    # Match the fixture loader's lock order (documents first).
    conn.exec_driver_sql("LOCK TABLE documents IN ROW EXCLUSIVE MODE")
    load_dictionaries(conn)
    sections = read_rows(data / "sections" / f"{doc_id}.json")
    authors = json.loads(
        (data / "sections" / f"{doc_id}.authors.json").read_text(encoding="utf-8")
    )
    for s in sections:
        author = authors.get(str(s["id"]), {})
        s["authors_raw"] = author.get("authors_raw")
        s["author_person_ids"] = author.get("person_ids") or []
    section_ids = assign_ids(conn, "sections", sections)
    for s in sections:
        if s["parent_id"] is not None:
            s["parent_id"] = section_ids[s["parent_id"]]

    chunks = read_rows(data / "chunks" / f"{doc_id}.jsonl")
    geometry = {
        g["chunk_id"]: g["lines"]
        for g in read_rows(data / "chunks" / f"{doc_id}.lines.jsonl")
    }
    for chunk in chunks:
        if chunk["id"] in geometry:
            chunk["lines"] = [
                {
                    "start": start,
                    "end": end,
                    "text": chunk["text"][start:end],
                    "bbox": [round(v, 4) for v in box],
                }
                for start, end, *box in geometry[chunk["id"]]
            ]
    chunk_ids = assign_ids(conn, "chunks", chunks)
    observations = read_rows(data / "observations" / f"{doc_id}.jsonl")
    assign_ids(conn, "observations", observations)
    for row in [*chunks, *observations]:
        if row["section_id"] is not None:
            row["section_id"] = section_ids[row["section_id"]]
    stamp = datetime.now(UTC).isoformat()
    links: list[dict[str, int]] = []
    for row in observations:
        row["chunk_id"] = chunk_ids[row["chunk_id"]]
        row.update(created_at=stamp, updated_at=stamp)
        links.extend(
            {"obs": row["id"], "person": person, "ord": n}
            for n, person in enumerate(row["person_ids"])
        )
    for table, rows in (
        ("sections", sections),
        ("chunks", chunks),
        ("observations", observations),
    ):
        if len(seed_table(conn, table, rows)) != len(rows):
            raise ProcessingError("Конфликт идентификаторов при публикации документа.")
    if links:
        conn.execute(
            text(
                "INSERT INTO observation_persons (observation_id,person_id,ord) VALUES (:obs,:person,:ord)"
            ),
            links,
        )
    conn.execute(
        text("UPDATE documents SET status='done', error=NULL WHERE id=:id"),
        {"id": doc_id},
    )


def process(document_id: int) -> None:
    """Run inside a fresh interpreter, which owns the document lock through publication."""
    engine = database_engine()
    try:
        with engine.connect() as conn:
            lock = {"key": f"letopisi.upload.{document_id}"}
            if not conn.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), lock
            ):
                return
            try:
                document = conn.scalar(
                    text("""UPDATE documents SET status='processing', error=NULL
                        WHERE id=:id AND status IN ('queued','processing','failed')
                        RETURNING to_jsonb(documents)"""),
                    {"id": document_id},
                )
                conn.commit()
                if document is None:
                    return
                storage = Path(os.environ.get("DATA_DIR") or ROOT / "data").resolve()
                source = storage / "raw" / str(document_id) / document["filename"]
                if not source.is_file():
                    raise ProcessingError(
                        "Оригинал загруженного документа отсутствует."
                    )
                with source.open("rb") as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != document["sha256"]
                    ):
                        raise ProcessingError(
                            "Контрольная сумма оригинала документа изменилась."
                        )
                data = storage / "uploads"
                os.environ["LETOPISI_DATA_DIR"] = str(data)
                os.environ.setdefault("LLM_CACHE_DIR", str(data / "llm_cache"))
                extract(source, data, document)
                publish(conn, data, document_id)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                log.exception("Ошибка обработки документа %s", document_id)
                message = (
                    str(exc)
                    if isinstance(exc, ProcessingError)
                    else "Не удалось сохранить результаты обработки."
                )
                conn.execute(
                    text(
                        "UPDATE documents SET status='failed',error=:error WHERE id=:id"
                    ),
                    {"id": document_id, "error": message},
                )
                conn.commit()
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), lock
                )
                conn.commit()
    finally:
        engine.dispose()


def run(document_id: int) -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "backend.app.jobs", str(document_id)],
            cwd=ROOT,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        log.exception("Не удалось запустить обработчик документа %s", document_id)
        engine = database_engine()
        try:
            with engine.begin() as conn:
                if conn.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": f"letopisi.upload.{document_id}"},
                ):
                    conn.execute(
                        text("""UPDATE documents SET status='failed',error='Ошибка запуска обработчика.'
                        WHERE id=:id AND status IN ('queued','processing')"""),
                        {"id": document_id},
                    )
        finally:
            engine.dispose()


def resume_uploads() -> None:
    raw = Path(os.environ.get("DATA_DIR") or ROOT / "data") / "raw"
    if not raw.is_dir():
        return
    engine = database_engine()
    try:
        with engine.connect() as conn:
            pending = conn.execute(
                text(
                    "SELECT id, filename FROM documents WHERE status IN ('queued','processing') ORDER BY id"
                )
            ).all()
        for doc_id, filename in pending:
            # POST stores originals in raw/<id>/. Stand and contract files use raw/<filename>.
            if (raw / str(doc_id) / filename).is_file():
                run(doc_id)
    finally:
        engine.dispose()


if __name__ == "__main__":
    process(int(sys.argv[1]))
