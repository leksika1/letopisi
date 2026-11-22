from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from .contract import api_error
from .db import apply_schema, database_engine
from .routers import documents, export, observations, queries, search

VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = database_engine()
    try:
        apply_schema(engine)
        app.state.engine = engine
        yield
    finally:
        engine.dispose()


app = FastAPI(title="Цифровые Летописи природы", version=VERSION, lifespan=lifespan)
for router in (
    search.router,
    documents.router,
    observations.router,
    queries.router,
    export.router,
):
    app.include_router(router)
for error_type in (HTTPException, RequestValidationError, IntegrityError, DataError):
    app.add_exception_handler(error_type, api_error)


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    db: bool
    version: str


@app.get("/api/health", operation_id="getHealth")
def health(request: Request) -> Health:
    engine: Engine = request.app.state.engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return Health(status="degraded", db=False, version=VERSION)
    return Health(status="ok", db=True, version=VERSION)
