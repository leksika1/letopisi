import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request
from sqlalchemy import text

from ..contract import validate
from ..records import detail

router = APIRouter(prefix="/api/observations")
Id = Annotated[int, Path(ge=1)]


def validate_dates(observation: dict[str, Any]) -> None:
    start, end = observation["date_from"], observation["date_to"]
    precision, year = observation["date_precision"], observation["obs_year"]
    if (start or end) and precision is None:
        raise HTTPException(422, "Для даты наблюдения нужна точность")
    if precision is not None and not (start or end or observation["date_raw"]):
        raise HTTPException(422, "Для выбранной точности нужна дата")
    if precision == "day" and start != end:
        raise HTTPException(
            422, "Для точности «день» даты начала и конца должны совпадать"
        )
    if observation["record_type"] == "publication":
        if year != observation["pub_year"]:
            raise HTTPException(
                422, "Год наблюдения должен совпадать с годом публикации"
            )
    elif start and year is not None and int(start[:4]) != year:
        raise HTTPException(
            422, "Год наблюдения должен совпадать с годом начальной даты"
        )


def validate_quote_flag(observation: dict[str, Any], chunk: dict[str, Any]) -> None:
    quote, span = observation["quote"], observation["quote_span"]
    if quote is None:
        return
    text = chunk["text"]
    if "quote_not_found" in observation["flags"]:
        if quote in text or span is not None or observation["bboxes"]:
            raise HTTPException(
                422, "Флаг quote_not_found противоречит источнику цитаты"
            )
    elif span is None or text[span[0] : span[1]] != quote:
        raise HTTPException(422, "Источник не подтверждает текст и границы цитаты")


def validate_edit(
    fields: dict[str, Any], candidate: dict[str, Any], previous: dict[str, Any]
) -> None:
    if fields.keys() & {
        "date_from",
        "date_to",
        "date_raw",
        "date_precision",
        "obs_year",
        "pub_year",
        "record_type",
    }:
        validate_dates(candidate)
    if (
        fields.keys() & {"record_type", "pub_title"}
        and candidate["record_type"] == "publication"
        and not candidate["pub_title"]
    ):
        raise HTTPException(422, "Для публикации нужно название")
    flags = set(candidate["flags"])
    if (
        fields.keys() & {"object_type", "taxon_id", "flags"}
        and candidate["object_type"] == "taxon"
        and candidate["taxon_id"] is None
        and flags.isdisjoint({"taxon_unmatched", "taxon_ambiguous"})
    ):
        raise HTTPException(
            422, "Укажите таксон или причину, по которой он не определён"
        )
    if (
        fields.keys() & {"value_raw", "flags"}
        and "value_not_in_quote" in flags
        and candidate["value_raw"]
        and candidate["value_raw"] in (candidate["quote"] or "")
    ):
        raise HTTPException(
            422, "Флаг value_not_in_quote противоречит значению в цитате"
        )
    if "flags" in fields and previous["chunk"] is not None:
        previous_flags = previous["observation"]["flags"]
        if ("quote_not_found" in flags) != ("quote_not_found" in previous_flags):
            validate_quote_flag(candidate, previous["chunk"])


@router.get("/{id}", operation_id="getObservation")
def get_observation(id: Id, request: Request) -> dict[str, Any]:
    with request.app.state.engine.connect() as conn:
        return detail(conn, id)


@router.patch("/{id}", operation_id="patchObservation")
def patch_observation(id: Id, body: dict[str, Any], request: Request) -> dict[str, Any]:
    validate("ObservationPatch", body)
    fields = dict(body.get("fields", {}))
    with request.app.state.engine.begin() as conn:
        if (
            conn.scalar(
                text("SELECT id FROM observations WHERE id=:id FOR UPDATE"), {"id": id}
            )
            is None
        ):
            raise HTTPException(404, "Наблюдение не найдено")
        previous = detail(conn, id)
        before = previous["observation"]
        after_status = {"edit": "edited", "verify": "verified", "reject": "rejected"}[
            body["action"]
        ]
        candidate = {**before, **fields, "status": after_status}
        candidate.pop("labels")
        validate("Observation", candidate)
        validate_edit(fields, candidate, previous)
        if fields.keys() & {"obs_year", "flags"}:
            book_year, obs_year = previous["document"]["year"], candidate["obs_year"]
            outside = (
                book_year is not None and obs_year is not None and book_year != obs_year
            )
            flags = list(candidate["flags"])
            if outside and "year_outside_book" not in flags:
                flags.append("year_outside_book")
            elif not outside and "year_outside_book" in flags:
                flags.remove("year_outside_book")
            fields["flags"] = flags
        changes = {
            k: {"old": before[k], "new": v} for k, v in fields.items() if before[k] != v
        }
        values = {k: v for k, v in fields.items() if k != "person_ids"}
        setters = [f"{k}=src.{k}" for k in values]
        # Keep edits in commit order even when transactions start out of order
        # or an imported edit came from a machine with a clock ahead of ours.
        setters.extend(
            [
                "status=:status",
                (
                    "updated_at=greatest(clock_timestamp(), o.updated_at, "
                    "(SELECT max(created_at) FROM observation_edits WHERE observation_id=o.id))"
                ),
            ]
        )
        edited_at = conn.execute(
            text(
                "UPDATE observations AS o SET "
                + ", ".join(setters)
                + " FROM jsonb_populate_record(NULL::observations, CAST(:data AS jsonb)) AS src "
                "WHERE o.id=:id RETURNING o.updated_at"
            ),
            {"id": id, "data": json.dumps(values), "status": after_status},
        ).scalar_one()
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
            (observation_id, action, editor, changes, status_before, status_after, comment, created_at)
            VALUES (:id,:action,:editor,CAST(:changes AS jsonb),:before,:after,:comment,:edited_at)"""),
            {
                "id": id,
                "action": body["action"],
                "editor": body["editor"],
                "changes": json.dumps(changes),
                "before": before["status"],
                "after": after_status,
                "comment": body.get("comment"),
                "edited_at": edited_at,
            },
        )
        return detail(conn, id)
