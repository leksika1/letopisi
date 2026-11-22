from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, text

# One row per observation. The ordered lateral aggregate preserves person_ids and
# avoids multiplying rows when several people co-authored the same observation.
RECORDS = """
SELECT to_jsonb(o) || jsonb_build_object('person_ids',coalesce(people.ids,'[]'::jsonb),
    'labels',jsonb_build_object('taxon_name_ru',t.name_ru,'taxon_name_lat',t.name_lat,
    'taxon_group',t.group_ru,'place_name',p.name,'phenophase_code',ph.code,
    'phenophase_name',ph.name_ru,'persons',coalesce(people.names,'[]'::jsonb),
    'document_title',d.title,'book_no',d.book_no,'book_year',d.year)) AS observation,
    to_jsonb(d) AS document, to_jsonb(p) AS place, to_jsonb(ph) AS phase,
    coalesce(people.surnames,'[]'::jsonb) AS surnames
FROM observations o
JOIN documents d ON d.id=o.document_id
LEFT JOIN taxa t ON t.id=o.taxon_id
LEFT JOIN places p ON p.id=o.place_id
LEFT JOIN phenophases ph ON ph.id=o.phenophase_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(person_id ORDER BY op.ord) ids,
           jsonb_agg(concat_ws(' ',ps.surname,ps.initials) ORDER BY op.ord) names,
           jsonb_agg(ps.surname ORDER BY op.ord) surnames
    FROM observation_persons op JOIN persons ps ON ps.id=op.person_id
    WHERE op.observation_id=o.id
) people ON true
"""


def detail(conn: Connection, obs_id: int) -> dict[str, Any]:
    row = (
        conn.execute(text(RECORDS + " WHERE o.id=:id"), {"id": obs_id})
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Наблюдение не найдено")
    obs = row["observation"]
    return {
        "observation": obs,
        "document": row["document"],
        "chunk": conn.scalar(
            text("SELECT to_jsonb(c) FROM chunks c WHERE id=:id"),
            {"id": obs["chunk_id"]},
        ),
        "section": conn.scalar(
            text("SELECT to_jsonb(s) FROM sections s WHERE id=:id"),
            {"id": obs["section_id"]},
        ),
        "edits": list(
            conn.scalars(
                text(
                    "SELECT to_jsonb(e) FROM observation_edits e WHERE observation_id=:id ORDER BY created_at,id"
                ),
                {"id": obs_id},
            )
        ),
    }
