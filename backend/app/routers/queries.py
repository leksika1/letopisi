import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, Response
from sqlalchemy import text

from ..contract import validate

router = APIRouter(prefix="/api/saved-queries")


@router.get("", operation_id="listSavedQueries")
def list_queries(request: Request) -> list[dict[str, Any]]:
    with request.app.state.engine.connect() as conn:
        return list(
            conn.scalars(text("SELECT to_jsonb(q) FROM saved_queries q ORDER BY id"))
        )


@router.post("", status_code=201, operation_id="createSavedQuery")
def save_query(body: dict[str, Any], request: Request) -> dict[str, Any]:
    validate("SavedQueryCreate", body)
    with request.app.state.engine.begin() as conn:
        return conn.execute(
            text("""INSERT INTO saved_queries(name,text,filters)
            VALUES (:name,:text,CAST(:filters AS jsonb)) RETURNING to_jsonb(saved_queries)"""),
            {
                "name": body["name"],
                "text": body.get("text"),
                "filters": json.dumps(body["filters"]),
            },
        ).scalar_one()


@router.delete("/{id}", status_code=204, operation_id="deleteSavedQuery")
def delete_query(id: Annotated[int, Path(ge=1)], request: Request) -> Response:
    with request.app.state.engine.begin() as conn:
        if (
            conn.scalar(
                text("DELETE FROM saved_queries WHERE id=:id RETURNING id"), {"id": id}
            )
            is None
        ):
            raise HTTPException(404, "Запрос не найден")
    return Response(status_code=204)
