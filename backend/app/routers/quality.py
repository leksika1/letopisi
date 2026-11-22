import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Never

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from ..contract import validator
from .documents import data_dir

router = APIRouter(prefix="/api")


def invalid_constant(value: str) -> Never:
    raise ValueError(f"Недопустимое число в JSON: {value}")


def read_json(path: Path) -> tuple[bytes, Any] | None:
    try:
        raw = path.read_bytes()
        return raw, json.loads(raw, parse_constant=invalid_constant)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise HTTPException(500, "Не удалось прочитать отчёт о качестве") from exc


def validated(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or not validator("Quality").is_valid(report):
        raise HTTPException(500, "Отчёт о качестве не соответствует схеме Quality")
    return report


def empty_report(ids: list[int], reason: str) -> dict[str, Any]:
    metric = {"field": "record", "precision": None, "recall": None, "f1": None, "n": 0}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "gold_version": reason,
        "extractor_version": "не определена",
        "is_placeholder": True,
        "n_gold_records": 0,
        "n_extracted_records": 0,
        "record_level": metric,
        "fields": [],
        "by_document": [
            {"document_id": doc_id, "record_level": metric} for doc_id in ids
        ],
        "error_examples": [],
        "unsupported": [],
    }


@router.get("/quality", operation_id="getQuality")
def quality(
    request: Request, document_id: Annotated[int | None, Query(ge=1)] = None
) -> dict[str, Any]:
    directory = data_dir()
    saved = read_json(directory / "quality.json")
    if saved is None:
        with request.app.state.engine.connect() as conn:
            ids = list(conn.scalars(text("SELECT id FROM documents ORDER BY id")))
        selected = ids if document_id is None else [i for i in ids if i == document_id]
        return empty_report(selected, "Отчёт по эталону ещё не рассчитан")

    raw, payload = saved
    report = validated(payload)
    if document_id is None:
        return report
    rows = [r for r in report["by_document"] if r["document_id"] == document_id]
    evidence = {
        key: [r for r in report[key] if r["document_id"] == document_id]
        for key in ("error_examples", "unsupported")
    }
    if (
        len(rows) == len(report["by_document"]) == 1
        and rows[0]["record_level"] == report["record_level"]
    ):
        # The evaluator only scores observations in the scope of the gold records.
        return {**report, **evidence}
    if rows:
        saved_scopes = read_json(directory / "quality.documents.json")
        if saved_scopes is not None:
            _, bundle = saved_scopes
            if not isinstance(bundle, dict) or not isinstance(
                bundle.get("reports"), dict
            ):
                raise HTTPException(500, "Некорректный набор отчётов по документам")
            if (
                bundle.get("report_sha256") == hashlib.sha256(raw).hexdigest()
                and str(document_id) in bundle["reports"]
            ):
                scoped = validated(bundle["reports"][str(document_id)])
                if scoped["by_document"] != rows or any(
                    r["document_id"] != document_id
                    for key in ("error_examples", "unsupported")
                    for r in scoped[key]
                ):
                    raise HTTPException(
                        500, "Отчёт по документу не соответствует общей оценке"
                    )
                return scoped
    # The aggregate alone does not contain per-document field denominators or
    # the count of extracted records in scope. Keep the known evidence scoped.
    missing = empty_report(
        [document_id] if rows else [], "Отдельная оценка этой книги недоступна"
    )
    return {**missing, **evidence}
