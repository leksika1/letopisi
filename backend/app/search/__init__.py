"""Поиск по наблюдениям: фильтры, разрешение строк через справочники, SQL, фенология,
виды места, подсказки, разбор запроса обычными словами.

Публичный интерфейс::

    from backend.app.search import search, SearchFilters
    page = search(conn, SearchFilters(taxon="лось", years=[2020]), page=1, page_size=50)
    # → {"items": [...ObservationListItem], "total": int, "page": int, "page_size": int}
"""
from .filters import FilterError, SearchFilters, SearchRequest, parse_query_params
from .phenology import NotFound, get_phenology
from .places import place_species
from .query_parse import parse_query
from .service import iter_all, search
from .suggest import suggest

__all__ = [
    "FilterError",
    "NotFound",
    "SearchFilters",
    "SearchRequest",
    "get_phenology",
    "iter_all",
    "parse_query",
    "parse_query_params",
    "place_species",
    "search",
    "suggest",
]
