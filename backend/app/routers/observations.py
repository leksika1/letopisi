import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request
from sqlalchemy import text

from ..contract import validate
from ..records import detail

router = APIRouter(prefix="/api/observations")
Id = Annotated[int, Path(ge=1)]


@router.get("/{id}", operation_id="getObservation")
def get_observation(id: Id, request: Request) -> dict[str, Any]:
    with request.app.state.engine.connect() as conn:
        return detail(conn, id)


@router.patch("/{id}", operation_id="patchObservation")
def patch_observation(id: Id, body: dict[str, Any], request: Request) -> dict[str, Any]:
    validate("ObservationPatch", body)
    fields = body.get("fields", {})
    with request.app.state.engine.begin() as conn:
        if (
            conn.scalar(
                text("SELECT id FROM observations WHERE id=:id FOR UPDATE"), {"id": id}
            )
            is None
        ):
            raise HTTPException(404, "Наблюдение не найдено")
        before = detail(conn, id)["observation"]
        after_status = {"edit": "edited", "verify": "verified", "reject": "rejected"}[
            body["action"]
        ]
        candidate = {**before, **fields, "status": after_status}
        candidate.pop("labels")
        validate("Observation", candidate)
        changes = {
            k: {"old": before[k], "new": v} for k, v in fields.items() if before[k] != v
        }
        values = {k: v for k, v in fields.items() if k != "person_ids"}
        setters = [f"{k}=src.{k}" for k in values]
        setters.extend(["status=:status", "updated_at=now()"])
        conn.execute(
            text(
                "UPDATE observations AS o SET "
                + ", ".join(setters)
                + " FROM jsonb_populate_record(NULL::observations, CAST(:data AS jsonb)) AS src WHERE o.id=:id"
            ),
            {"id": id, "data": json.dumps(values), "status": after_status},
        )
        if "person_ids" in changes:
            conn.execute(
                text("DELETE FROM observation_persons WHERE observation_id=:id"),
                {"id": id},
            )
            for n, person in enumerate(fields["person_ids"]):
                conn.execute(
                    text(
                        "INSERT INTO observation_persons(observation_id,person_id,ord) VALUES (:id,:pid,:n)"
                    ),
                    {"id": id, "pid": person, "n": n},
                )
        conn.execute(
            text("""INSERT INTO observation_edits
            (observation_id, action, editor, changes, status_before, status_after, comment)
            VALUES (:id,:action,:editor,CAST(:changes AS jsonb),:before,:after,:comment)"""),
            {
                "id": id,
                "action": body["action"],
                "editor": body["editor"],
                "changes": json.dumps(changes),
                "before": before["status"],
                "after": after_status,
                "comment": body.get("comment"),
            },
        )
        return detail(conn, id)
