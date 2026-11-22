"""Фильтры поиска: pydantic-модель ``SearchFilters`` строго по контракту
(``contracts/observation.schema.json#/$defs/SearchFilters``) и разбор query-параметров
GET /api/observations и /api/export.

В query массивы (``person``, ``years``, ``doc_years``) передаются через запятую
(``style: form, explode: false``). Для годов дополнительно понимаются диапазоны:
``years=1995-1996`` → [1995, 1996], ``years=1995-1997,2020`` → [1995, 1996, 1997, 2020].
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

RecordType = Literal["observation", "env_measurement", "publication"]
Status = Literal["auto", "verified", "edited", "rejected"]
TaxonGroup = Literal[
    "сосудистые растения", "мхи", "лишайники", "грибы", "птицы",
    "млекопитающие", "рыбы", "амфибии и рептилии", "насекомые", "прочие беспозвоночные",
]
SortField = Literal[
    "date_from", "obs_year", "object_raw", "toponym_raw", "confidence", "status", "id", "created_at", "updated_at",
]
Order = Literal["asc", "desc"]
Id = Annotated[int, Field(ge=1)]
Year = Annotated[int, Field(ge=1800, le=2100)]

DEFAULT_PAGE_SIZE = 50
MAX_API_PAGE_SIZE = 500  # предел для GET /observations (openapi.yaml)
MAX_PAGE_SIZE = 10_000  # предел для внутренних вызовов (экспорт B-3)
_MAX_RANGE = 300  # защита от «years=1800-2100» × N


class SearchFilters(BaseModel):
    """Фильтры поиска. Все поля необязательны; фильтры объединяются по И, значения внутри
    массива — по ИЛИ (contracts/README.md, «Решения», п. 9)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    q: str | None = None
    taxon_id: Id | None = None
    taxon: str | None = None
    group: TaxonGroup | None = None
    person: list[Annotated[str, Field(min_length=1)]] | None = Field(default=None, min_length=1)
    place_id: Id | None = None
    place: str | None = None
    phenophase_id: Id | None = None
    indicator: str | None = None
    record_type: RecordType | None = None
    document_id: Id | None = None
    date_from: date | None = None
    date_to: date | None = None
    years: list[Year] | None = Field(default=None, min_length=1)
    doc_years: list[Year] | None = Field(default=None, min_length=1)
    status: Status | None = None
    flagged: bool | None = None
    sort: SortField | None = None
    order: Order | None = None

    @field_validator("q", "taxon", "place", "indicator", mode="after")
    @classmethod
    def _empty_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("years", "doc_years", mode="after")
    @classmethod
    def _uniq_years(cls, v: list[int] | None) -> list[int] | None:
        return sorted(set(v)) if v else v

    @field_validator("person", mode="after")
    @classmethod
    def _uniq_person(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return v
        out: list[str] = []
        for s in v:
            if s not in out:
                out.append(s)
        return out

    @model_validator(mode="after")
    def _check_dates(self) -> SearchFilters:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from позже date_to")
        return self

    def to_json(self) -> dict[str, Any]:
        """Только заданные поля, в JSON-виде (даты — строками ISO). Для сохранённых запросов и /query/parse."""
        return self.model_dump(mode="json", exclude_none=True)


class SearchRequest(SearchFilters):
    """Фильтры + пагинация (то, что приходит в GET /api/observations)."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_API_PAGE_SIZE)

    def filters(self) -> SearchFilters:
        return SearchFilters.model_validate(self.model_dump(exclude={"page", "page_size"}, exclude_none=True))


# --------------------------------------------------------------------------- разбор query-параметров

_FILTER_FIELDS = set(SearchFilters.model_fields)
_LIST_FIELDS = {"person", "years", "doc_years"}
_YEAR_FIELDS = {"years", "doc_years"}


def parse_year_list(raw: str | list[str] | None) -> list[int] | None:
    """«1995,1996» / «1995-1996» / «1995–1997, 2020» / ['1995', '1996'] → отсортированный список годов."""
    if raw is None:
        return None
    parts: list[str] = []
    for item in raw if isinstance(raw, list) else [raw]:
        parts.extend(p.strip() for p in str(item).split(","))
    years: set[int] = set()
    for p in parts:
        if not p:
            continue
        m = re.fullmatch(r"(\d{4})\s*(?:-|–|—|\.\.)\s*(\d{4})", p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            if b - a > _MAX_RANGE:
                raise ValueError(f"слишком широкий диапазон годов: {p}")
            years.update(range(a, b + 1))
            continue
        if not re.fullmatch(r"\d{4}", p):
            raise ValueError(f"не год: {p!r}")
        years.add(int(p))
    return sorted(years) or None


def parse_str_list(raw: str | list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for item in raw if isinstance(raw, list) else [raw]:
        for p in str(item).split(","):
            p = p.strip()
            if p and p not in out:
                out.append(p)
    return out or None


def _parse_bool(v: Any) -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("", "null"):
        return None
    if s in ("1", "true", "yes", "on", "да"):
        return True
    if s in ("0", "false", "no", "off", "нет"):
        return False
    raise ValueError(f"не логическое значение: {v!r}")


def parse_query_params(params: Mapping[str, Any], *, with_paging: bool = True) -> SearchRequest | SearchFilters:
    """Query-параметры (dict или starlette QueryParams) → ``SearchRequest`` (или ``SearchFilters``).

    Повторяющиеся параметры (``years=1995&years=1996``) тоже понимаются. Пустые строки считаются
    отсутствующими. Неизвестные параметры → ошибка валидации (как ``additionalProperties: false``),
    кроме ``format`` (параметр экспорта).
    """
    getlist = getattr(params, "getlist", None)
    data: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    allowed = _FILTER_FIELDS | ({"page", "page_size"} if with_paging else set())
    for key in params:
        if key == "format":
            continue
        values = getlist(key) if getlist else params[key]
        if key not in allowed:
            errors.append({"loc": ["query", key], "msg": "неизвестный параметр", "type": "extra_forbidden"})
            continue
        try:
            if key in _YEAR_FIELDS:
                v = parse_year_list(values)
            elif key in _LIST_FIELDS:
                v = parse_str_list(values)
            else:
                if isinstance(values, list):
                    values = values[-1] if values else None
                v = values if values not in ("", None) else None
                if key == "flagged":
                    v = _parse_bool(v)
        except ValueError as e:
            errors.append({"loc": ["query", key], "msg": str(e), "type": "value_error"})
            continue
        if v is not None:
            data[key] = v
    if errors:
        raise FilterError("Некорректные параметры фильтра", errors)
    model = SearchRequest if with_paging else SearchFilters
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise FilterError("Некорректные параметры фильтра", _pyd_errors(e)) from e


def coerce_filters(filters: SearchFilters | Mapping[str, Any] | None) -> SearchFilters:
    """SearchFilters / dict (например filters сохранённого запроса) / None → SearchFilters."""
    if filters is None:
        return SearchFilters()
    if isinstance(filters, SearchFilters):
        return filters
    try:
        return SearchFilters.model_validate(dict(filters))
    except ValidationError as e:
        raise FilterError("Некорректные фильтры", _pyd_errors(e)) from e


class FilterError(ValueError):
    """Ошибка валидации фильтров; ``details`` — список ошибок по полям (для ответа 422)."""

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []


def _pyd_errors(e: ValidationError) -> list[dict[str, Any]]:
    out = []
    for err in e.errors(include_url=False, include_context=False):
        out.append({"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")})
    return out
