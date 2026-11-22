import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any
from zipfile import BadZipFile, ZipFile

import pymupdf
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from sqlalchemy import Connection, text

from pipeline.ingest import jobs

from ..db import ROOT

router = APIRouter(prefix="/api/documents")
Id = Annotated[int, PathParam(ge=1)]
MIME = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR") or ROOT / "data")


def document(conn: Connection, identifier: int) -> dict[str, Any]:
    row = conn.scalar(
        text("SELECT to_jsonb(d) FROM documents d WHERE id=:id"), {"id": identifier}
    )
    if row is None:
        raise HTTPException(404, "Документ не найден")
    return row


def original(doc: dict[str, Any]) -> Path:
    raw = (data_dir() / "raw").resolve()
    name = doc["filename"]
    if name != Path(name).name or "/" in name or "\\" in name or ":" in name:
        raise HTTPException(404, "Файл не найден")
    for folder in (raw / str(doc["id"]), raw):
        p = (folder / name).resolve()
        if p.is_relative_to(raw) and p.is_file():
            return p
    raise HTTPException(404, "Оригинал отсутствует в хранилище")


@router.get("", operation_id="listDocuments")
def list_documents(request: Request) -> list[dict[str, Any]]:
    with request.app.state.engine.connect() as conn:
        return list(
            conn.scalars(text("SELECT to_jsonb(d) FROM documents d ORDER BY id"))
        )


def inspect_file(path: Path, fmt: str) -> tuple[int | None, bool, str | None]:
    if fmt == "pdf":
        try:
            with pymupdf.open(path) as doc:
                if not doc.is_pdf or not doc.page_count or doc.needs_pass:
                    raise ValueError
                first = doc[0]
                return (
                    doc.page_count,
                    any(len(page.get_text().strip()) >= 50 for page in doc),
                    ("spread" if first.rect.width > first.rect.height else "single"),
                )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(415, "Файл не является доступным PDF") from exc
    member = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}[fmt]
    try:
        with ZipFile(path) as container:
            if member not in container.namelist():
                raise HTTPException(415, "Содержимое файла не соответствует формату")
    except BadZipFile as exc:
        raise HTTPException(415, "Повреждённый офисный документ") from exc
    return None, False, None


@router.post("", status_code=201, operation_id="uploadDocument")
def upload(
    request: Request,
    tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    year: Annotated[int, Form(ge=1800, le=2100)],
    title: Annotated[str, Form(min_length=1)],
    book_no: Annotated[int | None, Form(ge=1)] = None,
) -> dict[str, Any]:
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    fmt = Path(name).suffix.lower().lstrip(".")
    if fmt not in MIME or ":" in name:
        raise HTTPException(415, "Поддерживаются PDF, DOCX и XLSX")
    raw = data_dir() / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    limit = int(os.getenv("MAX_UPLOAD_BYTES", str(256 * 1024 * 1024)))
    size = 0
    placed = None
    with NamedTemporaryFile(dir=raw, delete=False) as tmp:
        stage = Path(tmp.name)
    try:
        with stage.open("wb") as stream:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > limit:
                    raise HTTPException(413, "Превышен лимит размера файла")
                stream.write(block)
                digest.update(block)
        checksum = digest.hexdigest()
        pages, layer, layout = inspect_file(stage, fmt)
        with request.app.state.engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:sha,0))"),
                {"sha": checksum},
            )
            if conn.scalar(
                text("SELECT id FROM documents WHERE sha256=:sha LIMIT 1"),
                {"sha": checksum},
            ):
                raise HTTPException(409, "Документ с таким содержимым уже загружен")
            row = conn.execute(
                text("""INSERT INTO documents
                (title,book_no,year,filename,format,pages,has_text_layer,layout,sha256,status)
                VALUES (:title,:book,:year,:filename,:fmt,:pages,:layer,:layout,:sha,'queued')
                RETURNING to_jsonb(documents)"""),
                {
                    "title": title,
                    "book": book_no,
                    "year": year,
                    "filename": name,
                    "fmt": fmt,
                    "pages": pages,
                    "layer": layer,
                    "layout": layout,
                    "sha": checksum,
                },
            ).scalar_one()
            folder = raw / str(row["id"])
            folder.mkdir(exist_ok=True)
            placed = folder / name
            stage.replace(placed)
    except BaseException:
        if placed is not None:
            placed.unlink(missing_ok=True)
        raise
    finally:
        stage.unlink(missing_ok=True)
    tasks.add_task(jobs.run, row["id"])
    return row


@router.get("/{id}/file", operation_id="getDocumentFile")
def get_file(id: Id, request: Request) -> FileResponse:
    with request.app.state.engine.connect() as conn:
        doc = document(conn, id)
    return FileResponse(
        original(doc), media_type=MIME[doc["format"]], filename=doc["filename"]
    )


@router.get("/{id}/pages/{n}.png", operation_id="getDocumentPage")
def get_page(
    id: Id, n: Id, request: Request, dpi: Annotated[int, Query(ge=50, le=300)] = 110
) -> Response:
    with request.app.state.engine.connect() as conn:
        doc = document(conn, id)
    if doc["format"] != "pdf":
        raise HTTPException(404, "У документа нет PDF-страниц")
    with pymupdf.open(original(doc)) as pdf:
        if n > pdf.page_count:
            raise HTTPException(404, "Страница не найдена")
        png = pdf[n - 1].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
    return Response(content=png, media_type="image/png")
