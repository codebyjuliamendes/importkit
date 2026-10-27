"""Header normalisation and mapping of CSV columns to contact fields."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from importkit.domain.errors import ImportRejectedError

_SEPARATORS = re.compile(r"[\s\-]+")

# Ordered by preference; the first header found wins.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "name": ("name", "full_name", "fullname", "contact_name", "contact", "nome", "nombre"),
    "email": ("email", "e_mail", "email_address", "mail", "e_mail_address", "correo"),
    "company": (
        "company",
        "organization",
        "organisation",
        "company_name",
        "org",
        "account",
        "empresa",
    ),
}


def normalize_header(value: object) -> str:
    """Canonical header key: trimmed, NFC, lower-case, spaces/dashes collapsed to ``_``."""
    text = value.strip() if isinstance(value, str) else ""
    text = unicodedata.normalize("NFC", text.lstrip("﻿")).strip().lower()
    return _SEPARATORS.sub("_", text)


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """Normalised header keys for each contact field; ``company`` may be unmapped."""

    name: str
    email: str
    company: str | None

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "email": self.email, "company": self.company or ""}


def normalize_headers(raw_headers: Sequence[str]) -> tuple[str, ...]:
    headers = tuple(normalize_header(h) for h in raw_headers)
    named = [h for h in headers if h]
    if len(set(named)) != len(named):
        raise ImportRejectedError("duplicate_columns", "The CSV contains duplicate column names.")
    return headers


def suggest_mapping(headers: Sequence[str]) -> dict[str, str]:
    """Best-effort field -> header guess from common synonyms (normalised headers)."""
    available = [h for h in headers if h]
    suggestion: dict[str, str] = {}
    used: set[str] = set()
    for field, synonyms in SYNONYMS.items():
        for candidate in synonyms:
            if candidate in available and candidate not in used:
                suggestion[field] = candidate
                used.add(candidate)
                break
    return suggestion


def resolve_mapping(
    headers: Sequence[str], requested: Mapping[str, str | None] | None
) -> FieldMapping:
    """Validate a user mapping against the file headers.

    Missing name/email fall back to the synonym suggestion. ``company`` is
    auto-suggested when absent (or ``None``); an explicit empty string leaves it
    unmapped, and an explicit column must exist.
    """
    requested = requested or {}
    suggestion = suggest_mapping(headers)
    known = set(headers)

    def pick(field: str) -> str:
        explicit = normalize_header(requested.get(field) or "")
        return explicit or suggestion.get(field, field)

    name, email = pick("name"), pick("email")
    if name not in known or email not in known:
        raise ImportRejectedError(
            "unmapped_required",
            "Choose a name and email column that exist in the CSV. Company is optional.",
        )
    if name == email:
        raise ImportRejectedError(
            "mapping_conflict", "Name and email must come from different columns."
        )
    company: str | None
    if "company" in requested and requested["company"] is not None:
        # Explicit choice; an empty string means "leave company unmapped".
        company = normalize_header(requested["company"]) or None
        if company is not None and company not in known:
            raise ImportRejectedError(
                "unknown_company_column",
                "The selected company column does not exist in the CSV.",
            )
    else:
        company = suggestion.get("company")
    if company in (name, email):
        company = None
    return FieldMapping(name=name, email=email, company=company)
