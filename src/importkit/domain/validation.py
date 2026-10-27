"""Row normalisation and validation with structured, per-cell errors."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from email_validator import EmailNotValidError, validate_email

from importkit.domain.csv_reader import CsvRecord
from importkit.domain.mapping import FieldMapping

NAME_MIN = 2
NAME_MAX = 100
EMAIL_MAX = 180
COMPANY_MAX = 100

_WHITESPACE = re.compile(r"\s+")
# C0/C1 control characters other than ordinary whitespace.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‍⁠﻿]")


class ErrorCode(StrEnum):
    COLUMN_COUNT = "column_count_mismatch"
    NAME_TOO_SHORT = "name_too_short"
    NAME_TOO_LONG = "name_too_long"
    EMAIL_INVALID = "email_invalid"
    EMAIL_TOO_LONG = "email_too_long"
    EMAIL_EXISTS = "email_exists"
    EMAIL_DUPLICATE = "email_duplicate_in_file"
    EMAIL_CONFLICT = "email_conflict"
    COMPANY_TOO_LONG = "company_too_long"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.COLUMN_COUNT: "This row has a different number of columns than the header.",
    ErrorCode.NAME_TOO_SHORT: f"Enter a name with at least {NAME_MIN} characters.",
    ErrorCode.NAME_TOO_LONG: f"Name is longer than {NAME_MAX} characters.",
    ErrorCode.EMAIL_INVALID: "Enter a valid email address.",
    ErrorCode.EMAIL_TOO_LONG: f"Email is longer than {EMAIL_MAX} characters.",
    ErrorCode.EMAIL_EXISTS: "This email already exists in the workspace.",
    ErrorCode.EMAIL_DUPLICATE: "This email is duplicated in the uploaded file.",
    ErrorCode.EMAIL_CONFLICT: "This email was imported by another request.",
    ErrorCode.COMPANY_TOO_LONG: f"Company is longer than {COMPANY_MAX} characters.",
}


@dataclass(frozen=True, slots=True)
class RowError:
    """A validation failure tied to a spreadsheet row and (when known) a source column."""

    row: int
    field: str
    column: str | None
    code: ErrorCode

    @property
    def message(self) -> str:
        return MESSAGES[self.code]

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "field": self.field,
            "column": self.column,
            "code": self.code.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ContactData:
    name: str
    email: str
    company: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "email": self.email, "company": self.company}


@dataclass(frozen=True, slots=True)
class RowResult:
    row_number: int
    data: ContactData
    errors: tuple[RowError, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        return "valid" if self.is_valid else "invalid"


def clean_text(value: str) -> str:
    """NFC-normalise, drop control/zero-width characters and collapse whitespace."""
    value = unicodedata.normalize("NFC", value)
    value = _CONTROL.sub("", value)
    return _WHITESPACE.sub(" ", value).strip()


def normalize_email(value: str, *, allow_test_domains: bool = False) -> str | None:
    """Return the canonical lower-case address, or ``None`` if it is not valid."""
    candidate = clean_text(value).replace(" ", "")
    if not candidate:
        return None
    try:
        result = validate_email(
            candidate,
            check_deliverability=False,
            test_environment=allow_test_domains,
        )
    except EmailNotValidError:
        return None
    return result.normalized.lower()


class _Validator:
    """Stateful pass over the records; ``seen`` tracks in-file duplicates."""

    def __init__(
        self,
        headers: Sequence[str],
        mapping: FieldMapping,
        existing_emails: Iterable[str],
        *,
        allow_test_domains: bool,
    ) -> None:
        self.index = {header: i for i, header in enumerate(headers) if header}
        self.width = len(headers)
        self.mapping = mapping
        self.existing = {email.lower() for email in existing_emails}
        self.seen: set[str] = set()
        self.allow_test_domains = allow_test_domains

    def _cell(self, values: tuple[str, ...], column: str | None) -> str:
        if column is None:
            return ""
        position = self.index[column]
        return values[position] if position < len(values) else ""

    def check(self, record: CsvRecord) -> RowResult:
        mapping, row = self.mapping, record.row_number
        name = clean_text(self._cell(record.values, mapping.name))
        raw_email = clean_text(self._cell(record.values, mapping.email))
        company = clean_text(self._cell(record.values, mapping.company))
        errors: list[RowError] = []

        if len(record.values) != self.width:
            errors.append(RowError(row, "row", None, ErrorCode.COLUMN_COUNT))
        if len(name) < NAME_MIN:
            errors.append(RowError(row, "name", mapping.name, ErrorCode.NAME_TOO_SHORT))
        elif len(name) > NAME_MAX:
            errors.append(RowError(row, "name", mapping.name, ErrorCode.NAME_TOO_LONG))

        email: str | None = None
        if len(raw_email) > EMAIL_MAX:
            errors.append(RowError(row, "email", mapping.email, ErrorCode.EMAIL_TOO_LONG))
        else:
            email = normalize_email(raw_email, allow_test_domains=self.allow_test_domains)
            if email is None:
                errors.append(RowError(row, "email", mapping.email, ErrorCode.EMAIL_INVALID))
        if email is not None:
            # Only well-formed addresses take part in duplicate detection.
            if email in self.existing:
                errors.append(RowError(row, "email", mapping.email, ErrorCode.EMAIL_EXISTS))
            elif email in self.seen:
                errors.append(RowError(row, "email", mapping.email, ErrorCode.EMAIL_DUPLICATE))
            self.seen.add(email)

        if len(company) > COMPANY_MAX:
            errors.append(RowError(row, "company", mapping.company, ErrorCode.COMPANY_TOO_LONG))

        data = ContactData(name=name, email=email or raw_email.lower(), company=company)
        return RowResult(row_number=row, data=data, errors=tuple(errors))


def validate_rows(
    records: Sequence[CsvRecord],
    headers: Sequence[str],
    mapping: FieldMapping,
    existing_emails: Iterable[str],
    *,
    allow_test_domains: bool = False,
) -> list[RowResult]:
    """Validate every record; the first occurrence of an email in the file wins."""
    validator = _Validator(headers, mapping, existing_emails, allow_test_domains=allow_test_domains)
    return [validator.check(record) for record in records]


def candidate_emails(
    records: Sequence[CsvRecord],
    headers: Sequence[str],
    mapping: FieldMapping,
    *,
    allow_test_domains: bool = False,
) -> set[str]:
    """Normalised addresses present in the file, used to query existing contacts."""
    position = {header: i for i, header in enumerate(headers) if header}[mapping.email]
    found: set[str] = set()
    for record in records:
        if position < len(record.values):
            raw = clean_text(record.values[position])
            if len(raw) <= EMAIL_MAX:
                email = normalize_email(raw, allow_test_domains=allow_test_domains)
                if email is not None:
                    found.add(email)
    return found
