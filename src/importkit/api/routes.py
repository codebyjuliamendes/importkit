"""HTTP endpoints. Contract-compatible with the original frontend."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from importkit import __version__, services
from importkit.api.schemas import (
    CommitOut,
    ContactOut,
    DashboardOut,
    ErrorOut,
    HealthOut,
    ImportDetailOut,
    ImportSummaryOut,
    InspectIn,
    InspectOut,
    MappingOut,
    PreviewIn,
    PreviewOut,
    RevertOut,
    rows_out,
)
from importkit.config import Settings
from importkit.domain.csv_reader import decode_bytes
from importkit.domain.errors import ImportRejectedError
from importkit.models import Import

router = APIRouter(prefix="/api")

ImportId = Annotated[int, Path(ge=1, le=2**53, description="Import (batch) id.")]

ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorOut},
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    413: {"model": ErrorOut},
    429: {"model": ErrorOut},
}

UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": PreviewIn.model_json_schema()},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "filename": {"type": "string"},
                        "nameColumn": {"type": "string"},
                        "emailColumn": {"type": "string"},
                        "companyColumn": {"type": "string"},
                    },
                }
            },
        },
    }
}


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class UploadPayload:
    __slots__ = ("filename", "mapping", "text")

    def __init__(self, text: str, filename: str | None, mapping: dict[str, str | None]) -> None:
        self.text = text
        self.filename = filename
        self.mapping = mapping


async def read_upload(request: Request) -> UploadPayload:
    """Accept either the JSON contract (``{filename, csv, mapping}``) or a multipart file."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "multipart/form-data":
        form = await request.form(max_files=1, max_fields=10)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ImportRejectedError("missing_file", "Attach a CSV file in the 'file' field.")
        raw = await upload.read()
        await upload.close()

        def field(name: str) -> str | None:
            value = form.get(name)
            return value if isinstance(value, str) else None

        return UploadPayload(
            decode_bytes(raw),
            field("filename") or upload.filename,
            {
                "name": field("nameColumn"),
                "email": field("emailColumn"),
                "company": field("companyColumn"),
            },
        )
    body = await request.body()
    try:
        payload = PreviewIn.model_validate_json(body or b"{}")
    except ValidationError as exc:
        raise ImportRejectedError(
            "invalid_request", "Send JSON with a 'csv' string, or upload a file."
        ) from exc
    mapping = payload.mapping.model_dump() if payload.mapping else {}
    return UploadPayload(payload.csv, payload.filename, mapping)


def _detail(record: Import) -> dict[str, Any]:
    return {
        **ImportSummaryOut.model_validate(record).model_dump(),
        "committed_at": record.committed_at,
        "reverted_at": record.reverted_at,
        "created_count": record.created_count,
        "mapping": MappingOut.model_validate(record.mapping_json or {}),
        "rows": rows_out(record.rows),
    }


@router.get("/health", response_model=HealthOut, tags=["ops"])
def health(session: SessionDep) -> JSONResponse | HealthOut:
    """Liveness + database reachability."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            {"status": "degraded", "database": "unavailable", "version": __version__},
            status_code=503,
        )
    return HealthOut(status="ok", database="ok", version=__version__)


@router.get("/dashboard", response_model=DashboardOut, tags=["read"])
def get_dashboard(session: SessionDep) -> dict[str, Any]:
    return services.dashboard(session)


@router.get("/contacts", response_model=list[ContactOut], tags=["read"])
def get_contacts(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Any]:
    return list(services.list_contacts(session, limit=limit, offset=offset))


@router.post(
    "/imports/inspect",
    response_model=InspectOut,
    responses=ERRORS,
    openapi_extra=UPLOAD_BODY,
    tags=["imports"],
)
async def inspect_import(request: Request, settings: SettingsDep) -> dict[str, Any]:
    """Detect delimiter and headers and suggest a mapping. Nothing is stored."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        text_value = (await read_upload(request)).text
    else:
        try:
            text_value = InspectIn.model_validate_json(await request.body() or b"{}").csv
        except ValidationError as exc:
            raise ImportRejectedError("invalid_request", "Send JSON with a 'csv' string.") from exc
    return await run_in_threadpool(services.inspect_csv, text_value, settings)


@router.post(
    "/imports/preview",
    status_code=201,
    response_model=PreviewOut,
    responses=ERRORS,
    openapi_extra=UPLOAD_BODY,
    tags=["imports"],
)
async def preview_import(request: Request, settings: SettingsDep) -> dict[str, Any]:
    """Validate a CSV and store a reviewable preview batch."""
    upload = await read_upload(request)

    def run() -> dict[str, Any]:
        with request.app.state.session_factory() as session:
            outcome = services.create_preview(
                session,
                text=upload.text,
                filename=upload.filename,
                mapping=upload.mapping,
                settings=settings,
            )
            return {**_detail(outcome.record), "delimiter": outcome.delimiter}

    return await run_in_threadpool(run)


@router.get(
    "/imports/{import_id}", response_model=ImportDetailOut, responses=ERRORS, tags=["imports"]
)
def get_import(import_id: ImportId, session: SessionDep) -> dict[str, Any]:
    return _detail(services.get_import(session, import_id))


@router.post(
    "/imports/{import_id}/commit", response_model=CommitOut, responses=ERRORS, tags=["imports"]
)
def commit_import(import_id: ImportId, session: SessionDep) -> CommitOut:
    """Insert the batch's valid rows. Idempotent: repeating returns the first result."""
    outcome = services.commit_import(session, import_id)
    return CommitOut(created=outcome.created, replayed=outcome.replayed)


@router.post(
    "/imports/{import_id}/revert", response_model=RevertOut, responses=ERRORS, tags=["imports"]
)
def revert_import(import_id: ImportId, session: SessionDep) -> RevertOut:
    """Remove exactly the contacts this batch created. Idempotent."""
    outcome = services.revert_import(session, import_id)
    return RevertOut(removed=outcome.removed, replayed=outcome.replayed)


@router.get(
    "/imports/{import_id}/errors.csv",
    responses={200: {"content": {"text/csv": {}}}, 404: {"model": ErrorOut}},
    response_class=Response,
    tags=["imports"],
)
def download_errors(import_id: ImportId, session: SessionDep) -> Response:
    """Invalid rows with their errors; cells are neutralised against formula injection."""
    report = services.error_report(session, import_id)
    return Response(
        content=report.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="import-errors-{import_id}.csv"'},
    )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def api_not_found(path: str) -> JSONResponse:
    del path
    return JSONResponse({"error": "API route not found.", "code": "not_found"}, status_code=404)
