from __future__ import annotations

import pytest

from importkit.domain.errors import ImportRejectedError
from importkit.domain.mapping import (
    normalize_header,
    normalize_headers,
    resolve_mapping,
    suggest_mapping,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Full Name", "full_name"),
        ("E-mail", "e_mail"),
        ("  Organization ", "organization"),
        ("﻿Name", "name"),
        ("Email  -  Address", "email_address"),
        (None, ""),
    ],
)
def test_normalize_header(raw: object, expected: str) -> None:
    assert normalize_header(raw) == expected


def test_duplicate_headers_rejected_after_normalisation() -> None:
    with pytest.raises(ImportRejectedError) as info:
        normalize_headers(["Email", "email "])
    assert info.value.code == "duplicate_columns"


def test_multiple_blank_headers_allowed() -> None:
    assert normalize_headers(["name", "", " "]) == ("name", "", "")


def test_suggest_mapping_uses_synonyms() -> None:
    headers = ("full_name", "e_mail", "organization")
    assert suggest_mapping(headers) == {
        "name": "full_name",
        "email": "e_mail",
        "company": "organization",
    }


def test_resolve_mapping_explicit() -> None:
    headers = ("full_name", "e_mail", "organization")
    mapping = resolve_mapping(
        headers, {"name": "Full Name", "email": "E-mail", "company": "Organization"}
    )
    assert mapping.as_dict() == {"name": "full_name", "email": "e_mail", "company": "organization"}


def test_resolve_mapping_defaults_to_suggestion() -> None:
    mapping = resolve_mapping(("name", "email"), None)
    assert (mapping.name, mapping.email, mapping.company) == ("name", "email", None)
    assert mapping.as_dict()["company"] == ""


def test_missing_required_column() -> None:
    with pytest.raises(ImportRejectedError) as info:
        resolve_mapping(("name", "phone"), {})
    assert info.value.code == "unmapped_required"


def test_explicit_company_must_exist() -> None:
    with pytest.raises(ImportRejectedError) as info:
        resolve_mapping(("name", "email"), {"company": "Company"})
    assert info.value.code == "unknown_company_column"


def test_name_and_email_must_differ() -> None:
    with pytest.raises(ImportRejectedError) as info:
        resolve_mapping(("name", "email"), {"name": "email", "email": "email"})
    assert info.value.code == "mapping_conflict"


def test_suggested_company_not_reused_for_name() -> None:
    mapping = resolve_mapping(("company", "email"), {"name": "company"})
    assert mapping.company is None


def test_explicit_empty_company_leaves_it_unmapped() -> None:
    headers = ("name", "email", "company")
    assert resolve_mapping(headers, {"company": ""}).company is None
    assert resolve_mapping(headers, {"company": None}).company == "company"
    assert resolve_mapping(headers, {}).company == "company"
