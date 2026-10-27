"""Application use cases: preview, commit, revert, reporting and read models."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from importkit.config import Settings
from importkit.db import begin_write
from importkit.domain.csv_reader import CsvTable, parse_csv
from importkit.domain.errors import ImportRejectedError
from importkit.domain.mapping import (
    FieldMapping,
    normalize_headers,
    resolve_mapping,
    suggest_mapping,
)
from importkit.domain.report import build_error_report
from importkit.domain.validation import (
    ErrorCode,
    RowError,
    RowResult,
    candidate_emails,
    clean_text,
    validate_rows,
)
from importkit.models import Contact, Import, ImportRow, ImportStatus, utcnow

DEFAULT_FILENAME = "contacts.csv"
FILENAME_MAX = 100
_LOOKUP_CHUNK = 500
_PATH_PARTS = re.compile(r"^.*[\\/]")


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PreviewOutcome:
    record: Import
    results: list[RowResult]
    mapping: FieldMapping
    delimiter: str


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    created: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class RevertOutcome:
    removed: int
    replayed: bool


def sanitize_filename(value: object) -> str:
    text = clean_text(value) if isinstance(value, str) else ""
    text = _PATH_PARTS.sub("", text).strip()
    return text[:FILENAME_MAX] or DEFAULT_FILENAME


def _check_size(text: str, settings: Settings) -> None:
    if not text.strip():
        raise ImportRejectedError("empty_file", "Upload a non-empty CSV file.")
    if len(text.encode("utf-8")) > settings.max_upload_bytes:
        kb = settings.max_upload_bytes // 1000
        raise ImportRejectedError(
            "file_too_large", f"Upload a non-empty CSV file up to {kb:,} KB.", status=413
        )


def _load_table(text: str, settings: Settings) -> tuple[CsvTable, tuple[str, ...]]:
    _check_size(text, settings)
    table = parse_csv(text, max_rows=settings.max_rows)
    return table, normalize_headers(table.headers)


def inspect_csv(text: str, settings: Settings, *, sample_size: int = 5) -> dict[str, Any]:
    """Describe an upload (headers, delimiter, mapping guess) without persisting it."""
    table, headers = _load_table(text, settings)
    return {
        "headers": [
            {"label": clean_text(raw) or f"Column {i + 1}", "key": key}
            for i, (raw, key) in enumerate(zip(table.headers, headers, strict=True))
        ],
        "delimiter": table.delimiter,
        "totalRows": len(table.records),
        "suggestedMapping": suggest_mapping(headers),
        "sample": [list(record.values) for record in table.records[:sample_size]],
    }


def existing_emails(session: Session, emails: Iterable[str]) -> set[str]:
    pending = sorted(set(emails))
    found: set[str] = set()
    for start in range(0, len(pending), _LOOKUP_CHUNK):
        chunk = pending[start : start + _LOOKUP_CHUNK]
        found.update(
            e.lower()
            for e in session.scalars(select(Contact.email).where(Contact.email.in_(chunk)))
        )
    return found


def create_preview(
    session: Session,
    *,
    text: str,
    filename: object,
    mapping: Mapping[str, str | None] | None,
    settings: Settings,
) -> PreviewOutcome:
    table, headers = _load_table(text, settings)
    field_mapping = resolve_mapping(headers, mapping)
    allow_test = settings.allow_test_email_domains
    emails = candidate_emails(table.records, headers, field_mapping, allow_test_domains=allow_test)
    known = existing_emails(session, emails)
    session.rollback()  # end the read transaction before taking the write lock

    results = validate_rows(
        table.records, headers, field_mapping, known, allow_test_domains=allow_test
    )
    valid = sum(1 for r in results if r.is_valid)
    record = Import(
        filename=sanitize_filename(filename),
        status=ImportStatus.PREVIEW.value,
        total_rows=len(results),
        valid_rows=valid,
        invalid_rows=len(results) - valid,
        mapping_json=field_mapping.as_dict(),
    )
    record.rows = [
        ImportRow(
            row_number=r.row_number,
            data_json=r.data.as_dict(),
            status=r.status,
            errors_json=[e.as_dict() for e in r.errors],
        )
        for r in results
    ]
    begin_write(session)
    session.add(record)
    session.commit()
    return PreviewOutcome(record, results, field_mapping, table.delimiter)


def _rowcount(result: object) -> int:
    return cast(CursorResult[Any], result).rowcount


def _insert_contacts(session: Session, import_id: int, rows: list[ImportRow]) -> int:
    """Insert contacts for valid rows; rows that lose a uniqueness race become invalid."""
    taken = existing_emails(session, (str(r.data_json.get("email", "")) for r in rows))
    insertable: list[ImportRow] = []
    for row in rows:
        if str(row.data_json.get("email", "")).lower() in taken:
            _mark_conflict(row)
        else:
            insertable.append(row)

    def build(row: ImportRow) -> Contact:
        data = row.data_json
        return Contact(
            import_id=import_id,
            name=str(data.get("name", "")),
            email=str(data.get("email", "")).lower(),
            company=str(data.get("company", "")),
        )

    try:
        with session.begin_nested():
            contacts = [build(row) for row in insertable]
            session.add_all(contacts)
            session.flush()
    except IntegrityError:
        # A concurrent commit won some emails; retry row by row.
        created = 0
        for row in insertable:
            contact = build(row)
            try:
                with session.begin_nested():
                    session.add(contact)
                    session.flush()
            except IntegrityError:
                _mark_conflict(row)
                continue
            row.contact_id = contact.id
            created += 1
        return created
    for row, contact in zip(insertable, contacts, strict=True):
        row.contact_id = contact.id
    return len(insertable)


def _mark_conflict(row: ImportRow) -> None:
    row.status = "invalid"
    row.errors_json = [RowError(row.row_number, "email", None, ErrorCode.EMAIL_CONFLICT).as_dict()]


def commit_import(session: Session, import_id: int) -> CommitOutcome:
    """Commit valid rows exactly once; repeating the call replays the first result."""
    begin_write(session)
    claimed = session.execute(
        update(Import)
        .where(Import.id == import_id, Import.status == ImportStatus.PREVIEW.value)
        .values(status=ImportStatus.COMMITTED.value, committed_at=utcnow())
    )
    if _rowcount(claimed) == 0:
        record = session.get(Import, import_id)
        session.rollback()
        if record is None:
            raise NotFoundError("Import not found.")
        if record.status == ImportStatus.COMMITTED.value:
            return CommitOutcome(created=record.created_count, replayed=True)
        raise ConflictError("Only a preview can be committed.")

    rows = list(
        session.scalars(
            select(ImportRow)
            .where(ImportRow.import_id == import_id, ImportRow.status == "valid")
            .order_by(ImportRow.row_number)
        )
    )
    created = _insert_contacts(session, import_id, rows)
    session.flush()
    valid, invalid = session.execute(
        select(
            func.coalesce(func.sum(case((ImportRow.status == "valid", 1), else_=0)), 0),
            func.coalesce(func.sum(case((ImportRow.status == "invalid", 1), else_=0)), 0),
        ).where(ImportRow.import_id == import_id)
    ).one()
    session.execute(
        update(Import)
        .where(Import.id == import_id)
        .values(valid_rows=valid, invalid_rows=invalid, created_count=created)
    )
    session.commit()
    return CommitOutcome(created=created, replayed=False)


def revert_import(session: Session, import_id: int) -> RevertOutcome:
    """Atomically remove the contacts created by one batch (idempotent)."""
    begin_write(session)
    claimed = session.execute(
        update(Import)
        .where(Import.id == import_id, Import.status == ImportStatus.COMMITTED.value)
        .values(status=ImportStatus.REVERTED.value, reverted_at=utcnow())
    )
    if _rowcount(claimed) == 0:
        record = session.get(Import, import_id)
        session.rollback()
        if record is None:
            raise NotFoundError("Import not found.")
        if record.status == ImportStatus.REVERTED.value:
            return RevertOutcome(removed=0, replayed=True)
        raise ConflictError("Only a committed import can be reverted.")
    removed = _rowcount(session.execute(delete(Contact).where(Contact.import_id == import_id)))
    session.execute(
        update(ImportRow).where(ImportRow.import_id == import_id).values(contact_id=None)
    )
    session.commit()
    return RevertOutcome(removed=removed, replayed=False)


def get_import(session: Session, import_id: int) -> Import:
    record = session.get(Import, import_id)
    if record is None:
        raise NotFoundError("Import not found.")
    return record


def dashboard(session: Session) -> dict[str, Any]:
    contacts = session.scalar(select(func.count()).select_from(Contact)) or 0
    imports = session.scalar(select(func.count()).select_from(Import)) or 0
    pending = (
        session.scalar(
            select(func.count())
            .select_from(Import)
            .where(Import.status == ImportStatus.PREVIEW.value)
        )
        or 0
    )
    rejected = (
        session.scalar(
            select(func.coalesce(func.sum(Import.invalid_rows), 0)).where(
                Import.status != ImportStatus.REVERTED.value
            )
        )
        or 0
    )
    latest = list(session.scalars(select(Import).order_by(Import.id.desc()).limit(6)))
    return {
        "contacts": contacts,
        "imports": imports,
        "pending": pending,
        "rejected": rejected,
        "latest": latest,
    }


def list_contacts(session: Session, *, limit: int, offset: int) -> list[Contact]:
    return list(
        session.scalars(select(Contact).order_by(Contact.id.desc()).limit(limit).offset(offset))
    )


def error_report(session: Session, import_id: int) -> str:
    get_import(session, import_id)
    rows = session.scalars(
        select(ImportRow)
        .where(ImportRow.import_id == import_id, ImportRow.status == "invalid")
        .order_by(ImportRow.row_number)
    )
    return build_error_report((r.row_number, r.data_json, r.errors_json) for r in rows)
