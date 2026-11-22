"""Эндпоинты поиска (contracts/openapi.yaml): GET /api/observations, POST /api/query/parse,
GET /api/phenology, GET /api/places/{id}/species, GET /api/suggest/{kind}.

Подключение в backend/app/main.py::

    from backend.app.routers.search import router as search_router
    app.include_router(search_router)

Префикс ``/api`` уже задан в роутере. Ошибки отдаются в формате контракта
``{"error": {"code", "message", "details"}}`` независимо от обработчиков приложения.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.search import query_parse, service
from backend.app.search.filters import FilterError, TaxonGroup, parse_query_params, parse_year_list
from backend.app.search.phenology import NotFound, get_phenology
from backend.app.search.places import place_species
from backend.app.search.suggest import KINDS
from backend.app.search.suggest import suggest as do_suggest

router = APIRouter(prefix="/api", tags=["search"])
get_conn = service.get_conn


def error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


class QueryParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


@router.get("/observations", summary="Поиск записей с фильтрами и пагинацией", tags=["observations"])
def list_observations(
    request: Request,
    q: str | None = Query(None, description="Свободный текст"),
    taxon_id: int | None = Query(None, ge=1),
    taxon: str | None = Query(None, description="Название вида строкой (рус./лат./синоним/род)"),
    group: TaxonGroup | None = Query(None),
    person: str | None = Query(None, description="Фамилии через запятую (ИЛИ)"),
    place_id: int | None = Query(None, ge=1),
    place: str | None = Query(None),
    phenophase_id: int | None = Query(None, ge=1),
    indicator: str | None = Query(None),
    record_type: str | None = Query(None),
    document_id: int | None = Query(None, ge=1),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    years: str | None = Query(None, description="Годы наблюдения через запятую; диапазон 1995-1996"),
    doc_years: str | None = Query(None, description="Годы книг через запятую"),
    status: str | None = Query(None),
    flagged: str | None = Query(None),
    sort: str | None = Query(None),
    order: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    conn=Depends(get_conn),
):
    # Явные параметры — для документации OpenAPI; разбираем исходные query (повторы, запятые, диапазоны).
    try:
        req = parse_query_params(request.query_params)
    except FilterError as e:
        return error(422, "validation_error", e.message, e.details)
    try:
        return service.search(conn, req)
    except FilterError as e:
        return error(422, "validation_error", e.message, e.details)


@router.post("/query/parse", summary="Разбор запроса обычными словами в фильтры")
def parse(body: dict[str, Any], conn=Depends(get_conn)):
    try:
        req = QueryParseRequest.model_validate(body)
    except ValidationError as e:
        return error(422, "validation_error", "Тело запроса: нужен {text}",
                     [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors(include_url=False)])
    return query_parse.parse_query(conn, req.text)


@router.get("/phenology", summary="Даты фенофаз вида по годам", tags=["analytics"])
def phenology(
    taxon_id: int = Query(..., ge=1),
    phenophase_id: int | None = Query(None, ge=1),
    years: str | None = Query(None),
    conn=Depends(get_conn),
):
    try:
        ys = parse_year_list(years) if years else None
    except ValueError as e:
        return error(422, "validation_error", str(e))
    try:
        return get_phenology(conn, taxon_id, phenophase_id, ys)
    except NotFound as e:
        return error(404, "not_found", e.message)


@router.get("/places/{id}/species", summary="Виды, отмеченные в месте", tags=["analytics"])
def species_in_place(
    id: int = Path(..., ge=1),
    year: int | None = Query(None),
    group: TaxonGroup | None = Query(None),
    conn=Depends(get_conn),
):
    try:
        return place_species(conn, id, year, group)
    except NotFound as e:
        return error(404, "not_found", e.message)


@router.get("/suggest/{kind}", summary="Подсказки для полей фильтров (pg_trgm)", tags=["dictionaries"])
def suggest(
    kind: str,
    q: str = Query(""),
    limit: int = Query(10),
    conn=Depends(get_conn),
):
    if kind not in KINDS:
        return error(400, "bad_request", f"kind должен быть одним из: {', '.join(KINDS)}")
    if not q.strip():
        return error(400, "bad_request", "параметр q обязателен и не может быть пустым")
    if not 1 <= limit <= 50:
        return error(400, "bad_request", "limit должен быть от 1 до 50")
    return do_suggest(conn, kind, q, limit)
