import csv
import json
from datetime import UTC, date, datetime
from io import BytesIO, StringIO
from itertools import batched
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import text

from ..db import ROOT
from ..records import RECORDS
from ..search import FilterError, iter_all, parse_query_params

router = APIRouter(prefix="/api")
HEADERS = [
    col["header"]
    for col in json.loads(
        (ROOT / "contracts" / "export_columns.json").read_text(encoding="utf-8")
    )
]
STATUS = {
    "auto": "авто",
    "verified": "подтверждено",
    "edited": "исправлено",
    "rejected": "отклонено",
}


def display_date(obs: dict[str, Any]) -> str:
    precision = obs["date_precision"]
    start, end = obs["date_from"], obs["date_to"]
    if start and precision in {"day", "range", "year"}:
        if precision == "year":
            return start[:4]
        first = date.fromisoformat(start).strftime("%d.%m.%Y")
        if precision == "range" and end:
            return first + "–" + date.fromisoformat(end).strftime("%d.%m.%Y")
        return first
    return obs["date_raw"] or ""


def export_row(row: dict[str, Any], base: str) -> tuple[list[Any], str]:
    o, doc = row["observation"], row["document"]
    labels, ph, place = o["labels"], row["phase"], row["place"]
    coords = o["coords_raw"]
    if not coords and place and place["lat"] is not None:
        coords = (
            f"{place['lat']}, {place['lon']} (приблизительно, {place['coords_source']})"
        )
    event = o["event_raw"]
    publication = o["record_type"] == "publication"
    if publication:
        event = "публикация: " + ", ".join(
            str(o[k])
            for k in ("pub_authors", "pub_title", "pub_venue", "pub_year")
            if o[k] is not None
        )
    phase = o["phenophase_raw"]
    if ph:
        phase = (
            (f"фаза {ph['code']}" + (f" - {ph['name_ru']}" if ph["name_ru"] else ""))
            if ph["applies_to"] == "plants"
            else ph["name_ru"]
        )
    pdf_page = o["locator"].get("pdf_page")
    page_name = f"PDF стр. {pdf_page}" if pdf_page else ""
    page = ", ".join(
        s
        for s in (
            o["printed_pages"] or page_name,
            f"том {doc['book_no']}" if doc["book_no"] else "",
        )
        if s
    )
    link = f"{base}/observations/{o['id']}" + (f"?page={pdf_page}" if pdf_page else "")
    confidence = STATUS[o["status"]] + (
        "; флаги: " + ", ".join(o["flags"]) if o["flags"] else ""
    )
    return [
        display_date(o),
        o["obs_year"],
        o["observers_raw"] or ", ".join(row["surnames"]),
        labels["taxon_name_ru"]
        or ((o["pub_title"] or o["object_raw"]) if publication else o["object_raw"]),
        labels["taxon_name_lat"],
        o["toponym_raw"] or labels["place_name"],
        coords or "нет",
        o["habitat_raw"],
        event,
        o["value_num"] if o["value_num"] is not None else o["value_raw"] or "нет",
        phase or "не фиксировалась",
        page,
        o["note"],
        link,
        confidence,
    ], ", ".join(s for s in (doc["title"], page_name) if s)


@router.get("/export", operation_id="exportObservations")
def export(request: Request, format: Literal["csv", "xlsx"]) -> Response:
    base = str(request.base_url).rstrip("/")
    rows: list[tuple[list[Any], str]] = []
    try:
        filters = parse_query_params(request.query_params, with_paging=False)
        # Keep pagination and export-only fields on the same database snapshot.
        with request.app.state.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn:
            for batch in batched(iter_all(conn, filters), 2000):
                ids = [item["id"] for item in batch]
                result = conn.execute(
                    text(RECORDS + " WHERE o.id=ANY(:ids)"), {"ids": ids}
                )
                by_id = {r["observation"]["id"]: dict(r) for r in result.mappings()}
                rows.extend(export_row(by_id[i], base) for i in ids)
    except FilterError as exc:
        raise HTTPException(400, exc.message) from exc
    if format == "csv":
        stream = StringIO(newline="")
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(HEADERS)
        for values, _ in rows:
            writer.writerow(values)
        data = stream.getvalue().encode("utf-8-sig")
        mime = "text/csv"
    else:
        book = Workbook()
        sheet = book.active
        sheet.title = "Наблюдения"
        sheet.append(HEADERS)
        widths = [len(h) for h in HEADERS]
        for row_number, (values, caption) in enumerate(rows, start=2):
            link = values[13]
            values[13] = caption
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row_number, column, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                if value is not None:
                    widths[column - 1] = max(widths[column - 1], len(str(value)))
            ref = sheet.cell(row_number, 14)
            ref.hyperlink, ref.style = link, "Hyperlink"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for i, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = min(width + 2, 80)
        binary = BytesIO()
        book.save(binary)
        data = binary.getvalue()
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="letopisi_export_{stamp}.{format}"'
        },
    )
