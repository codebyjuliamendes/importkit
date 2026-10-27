from __future__ import annotations

from importkit.domain.csv_reader import CsvRecord
from importkit.domain.mapping import FieldMapping
from importkit.domain.validation import (
    ErrorCode,
    candidate_emails,
    clean_text,
    normalize_email,
    validate_rows,
)

HEADERS = ("name", "email", "company")
MAPPING = FieldMapping(name="name", email="email", company="company")


def rec(row: int, *values: str) -> CsvRecord:
    return CsvRecord(row_number=row, values=values)


def codes(result: object) -> list[str]:
    return [e.code.value for e in result.errors]  # type: ignore[attr-defined]


def test_valid_row_is_normalised() -> None:
    [result] = validate_rows(
        [rec(2, "  Avery   Stone ", " Avery@Example.COM ", " Northstar​ ")],
        HEADERS,
        MAPPING,
        [],
    )
    assert result.is_valid
    assert result.status == "valid"
    assert result.data.as_dict() == {
        "name": "Avery Stone",
        "email": "avery@example.com",
        "company": "Northstar",
    }


def test_name_length_rules() -> None:
    short, long_ = validate_rows(
        [rec(2, "A", "a@example.com", ""), rec(3, "x" * 101, "b@example.com", "")],
        HEADERS,
        MAPPING,
        [],
    )
    assert codes(short) == ["name_too_short"]
    assert codes(long_) == ["name_too_long"]


def test_email_rules() -> None:
    rows = validate_rows(
        [
            rec(2, "Avery", "not-an-email", ""),
            rec(3, "Avery", "", ""),
            rec(4, "Avery", "a" * 175 + "@x.com", ""),
            rec(5, "Avery", "a@b", ""),
        ],
        HEADERS,
        MAPPING,
        [],
    )
    assert [codes(r) for r in rows] == [
        ["email_invalid"],
        ["email_invalid"],
        ["email_too_long"],
        ["email_invalid"],
    ]


def test_errors_are_structured() -> None:
    [result] = validate_rows([rec(7, "Avery", "nope", "")], HEADERS, MAPPING, [])
    error = result.errors[0].as_dict()
    assert error == {
        "row": 7,
        "field": "email",
        "column": "email",
        "code": "email_invalid",
        "message": "Enter a valid email address.",
    }


def test_company_too_long() -> None:
    [result] = validate_rows([rec(2, "Avery", "a@example.com", "c" * 101)], HEADERS, MAPPING, [])
    assert codes(result) == ["company_too_long"]


def test_duplicates_in_file_first_wins_case_insensitive() -> None:
    first, second = validate_rows(
        [rec(2, "Avery", "avery@example.com", ""), rec(3, "Taylor", "AVERY@example.com", "")],
        HEADERS,
        MAPPING,
        [],
    )
    assert first.is_valid
    assert codes(second) == ["email_duplicate_in_file"]


def test_blank_emails_are_not_reported_as_duplicates() -> None:
    rows = validate_rows([rec(2, "Avery", "", ""), rec(3, "Taylor", "", "")], HEADERS, MAPPING, [])
    assert [codes(r) for r in rows] == [["email_invalid"], ["email_invalid"]]


def test_existing_emails_rejected() -> None:
    [result] = validate_rows(
        [rec(2, "Avery", "Avery@Example.com", "")], HEADERS, MAPPING, ["AVERY@example.com"]
    )
    assert codes(result) == ["email_exists"]


def test_column_count_mismatch_is_a_row_error() -> None:
    [result] = validate_rows([rec(2, "Avery", "a@example.com")], HEADERS, MAPPING, [])
    assert codes(result) == [ErrorCode.COLUMN_COUNT.value]
    assert result.data.company == ""


def test_unmapped_company_is_empty() -> None:
    mapping = FieldMapping(name="name", email="email", company=None)
    [result] = validate_rows([rec(2, "Avery", "a@example.com", "ACME")], HEADERS, mapping, [])
    assert result.is_valid
    assert result.data.company == ""


def test_reserved_test_domains_are_configurable() -> None:
    assert normalize_email("a@northstar.test") is None
    assert normalize_email("a@northstar.test", allow_test_domains=True) == "a@northstar.test"


def test_normalize_email_handles_unicode_and_blanks() -> None:
    assert normalize_email("   ") is None
    assert normalize_email("José@Exämple.com") == "josé@exämple.com"


def test_clean_text_strips_control_characters() -> None:
    assert clean_text("A\x00ve​ry\t\nStone") == "Avery Stone"


def test_candidate_emails() -> None:
    records = [
        rec(2, "A", "A@example.com", ""),
        rec(3, "B", "bad", ""),
        rec(4, "C"),
        rec(5, "D", "x" * 200, ""),
    ]
    assert candidate_emails(records, HEADERS, MAPPING) == {"a@example.com"}
