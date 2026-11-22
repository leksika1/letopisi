import json
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.exc import DataError, IntegrityError

from .db import ROOT

DEFS = json.loads(
    (ROOT / "contracts" / "observation.schema.json").read_text(encoding="utf-8")
)["$defs"]


@lru_cache
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$defs": DEFS, "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker()
    )


def validate(name: str, value: Any) -> None:
    error = next(validator(name).iter_errors(value), None)
    if error:
        raise HTTPException(
            422, f"Данные не соответствуют {name} ({error.json_path}): {error.message}"
        )


async def api_error(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        status, message = exc.status_code, str(exc.detail)
    elif isinstance(exc, (IntegrityError, DataError)):
        status, message = 422, "Значение нарушает ограничение базы данных"
    elif isinstance(exc, RequestValidationError):
        if (
            getattr(request.scope.get("route"), "operation_id", None)
            == "exportObservations"
        ):
            status, message = 400, "Укажите формат выгрузки csv или xlsx"
        else:
            status, message = 422, "Проверьте обязательные поля и типы параметров"
    else:
        status, message = 500, "Ошибка сервиса"
    codes = {
        400: "bad_request",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
    }
    return JSONResponse(
        status_code=status,
        content={"error": {"code": codes.get(status, "internal"), "message": message}},
    )
