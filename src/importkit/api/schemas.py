"""HTTP request/response contracts (camelCase on the wire, as the frontend expects)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


UtcDateTime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]
OptionalUtcDateTime = Annotated[datetime | None, PlainSerializer(_iso_utc, return_type=str | None)]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        from_attributes=True,
    )


class ErrorOut(ApiModel):
    error: str
    code: str | None = None
    request_id: str | None = None


class MappingIn(ApiModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)


class InspectIn(ApiModel):
    csv: str


class PreviewIn(ApiModel):
    model_config = ConfigDict(extra="ignore")

    filename: str | None = Field(default=None, max_length=1000)
    csv: str
    mapping: MappingIn | None = None


class RowErrorOut(ApiModel):
    row: int | None = None
    field: str
    column: str | None = None
    code: str | None = None
    message: str


class ContactDataOut(ApiModel):
    name: str = ""
    email: str = ""
    company: str = ""


class RowOut(ApiModel):
    row_number: int
    status: str
    data: ContactDataOut
    errors: list[RowErrorOut]


class ImportSummaryOut(ApiModel):
    id: int
    filename: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    created_at: UtcDateTime


class MappingOut(ApiModel):
    name: str = ""
    email: str = ""
    company: str = ""


class ImportDetailOut(ImportSummaryOut):
    committed_at: OptionalUtcDateTime = None
    reverted_at: OptionalUtcDateTime = None
    created_count: int = 0
    mapping: MappingOut
    rows: list[RowOut]


class PreviewOut(ImportDetailOut):
    delimiter: str


class HeaderOut(ApiModel):
    label: str
    key: str


class InspectOut(ApiModel):
    headers: list[HeaderOut]
    delimiter: str
    total_rows: int
    suggested_mapping: dict[str, str]
    sample: list[list[str]]


class DashboardOut(ApiModel):
    contacts: int
    imports: int
    pending: int
    rejected: int
    latest: list[ImportSummaryOut]


class ContactOut(ApiModel):
    id: int
    name: str
    email: str
    company: str
    created_at: UtcDateTime


class CommitOut(ApiModel):
    ok: bool = True
    created: int
    replayed: bool = Field(description="True when the batch was already committed.")


class RevertOut(ApiModel):
    ok: bool = True
    removed: int
    replayed: bool = Field(description="True when the batch was already reverted.")


class HealthOut(ApiModel):
    status: str
    database: str
    version: str


def rows_out(rows: list[Any]) -> list[RowOut]:
    return [
        RowOut(
            row_number=row.row_number,
            status=row.status,
            data=ContactDataOut.model_validate(row.data_json or {}),
            errors=[RowErrorOut.model_validate(e) for e in (row.errors_json or [])],
        )
        for row in rows
    ]
