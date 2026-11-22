"""Сервисный слой поиска: публичная функция ``search`` и обёртки для роутера.

``search(conn, filters, page=1, page_size=50)`` → ``{"items", "total", "page", "page_size"}``;
items — в форме ``ObservationListItem`` (запись + ``labels``). ``conn`` — SQLAlchemy ``Connection``
(также принимаются ``Engine`` и ``Session``). Экспорт (B-3) вызывает ``search`` с ``page_size``
до 10 000.

Зависимость соединения для роутера: ``get_conn`` из ``backend.app.db``, если модуль есть,
иначе локальный запасной вариант ниже (engine по ``DATABASE_URL``).
"""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from .filters import MAX_PAGE_SIZE, FilterError, SearchFilters, SearchRequest, coerce_filters
from .sql import prepare, row_to_item, run_page


@contextmanager
def _connection(conn) -> Iterator[Any]:
    """Connection как есть; Session → её Connection; Engine → новое соединение на время вызова."""
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.orm import Session

    if isinstance(conn, Connection):
        yield conn
    elif isinstance(conn, Session):
        yield conn.connection()
    elif isinstance(conn, Engine):
        with conn.connect() as c:
            yield c
    else:
        raise TypeError("search: ожидается SQLAlchemy Connection, Session или Engine")


def search(conn, filters: SearchFilters | Mapping[str, Any] | None = None, page: int | None = None,
           page_size: int | None = None) -> dict[str, Any]:
    """Поиск записей по фильтрам. Если ``filters`` — ``SearchRequest`` (с пагинацией),
    его page/page_size используются, когда аргументы не заданы явно."""
    if isinstance(filters, SearchRequest):
        page = page or filters.page
        page_size = page_size or filters.page_size
        flt = filters.filters()
    elif isinstance(filters, Mapping) and ("page" in filters or "page_size" in filters):
        d = dict(filters)
        page = page or int(d.pop("page", 1) or 1)
        page_size = page_size or int(d.pop("page_size", 50) or 50)
        flt = coerce_filters(d)
    else:
        flt = coerce_filters(filters)
    page = int(page or 1)
    page_size = int(page_size or 50)
    if page < 1:
        raise FilterError("page должен быть ≥ 1", [{"loc": ["page"], "msg": "≥ 1", "type": "value_error"}])
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise FilterError(f"page_size должен быть от 1 до {MAX_PAGE_SIZE}",
                          [{"loc": ["page_size"], "msg": f"1..{MAX_PAGE_SIZE}", "type": "value_error"}])
    with _connection(conn) as c:
        pr = prepare(c, flt)
        rows, total = run_page(c, pr, page, page_size)
    return {"items": [row_to_item(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def iter_all(conn, filters: SearchFilters | Mapping[str, Any] | None = None,
             chunk: int = 2000) -> Iterator[dict[str, Any]]:
    """Все записи по фильтрам, порциями (для выгрузки больше 10 000 строк)."""
    page = 1
    while True:
        res = search(conn, filters, page=page, page_size=chunk)
        yield from res["items"]
        if page * chunk >= res["total"] or not res["items"]:
            return
        page += 1


# --------------------------------------------------------------------------- соединение для роутера

def _db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("не задан DATABASE_URL")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache(maxsize=1)
def _engine():
    from sqlalchemy import create_engine

    return create_engine(_db_url(), pool_pre_ping=True, future=True)


def fallback_get_conn() -> Iterator[Any]:
    """Запасной ``get_conn`` (если каркаса backend.app.db ещё нет): соединение на запрос."""
    with _engine().connect() as conn:
        yield conn


try:  # каркас другого участника (B-1)
    from backend.app.db import get_conn  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 — модуля нет или в нём нет get_conn
    get_conn = fallback_get_conn
