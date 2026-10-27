from __future__ import annotations

import csv
import io

import pytest

from importkit.domain.report import build_error_report, format_errors, neutralize_cell


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_triggers_are_neutralised(prefix: str) -> None:
    assert neutralize_cell(prefix + "SUM(A1)") == "'" + prefix + "SUM(A1)"


@pytest.mark.parametrize("value", ["Northstar", "", " =x", "a=b"])
def test_safe_values_untouched(value: str) -> None:
    assert neutralize_cell(value) == value


def test_none_becomes_empty() -> None:
    assert neutralize_cell(None) == ""


def test_report_layout() -> None:
    report = build_error_report(
        [
            (
                3,
                {"name": "Taylor", "email": "bad", "company": '=HYPERLINK("x")'},
                [{"field": "email", "message": "Enter a valid email address."}],
            )
        ]
    )
    assert report.startswith("﻿")
    assert "\r\n" in report
    rows = list(csv.reader(io.StringIO(report.lstrip("﻿"), newline="")))
    assert rows[0] == ["row_number", "name", "email", "company", "errors"]
    assert rows[1] == [
        "3",
        "Taylor",
        "bad",
        '\'=HYPERLINK("x")',
        "email: Enter a valid email address.",
    ]
    assert '"\'=HYPERLINK(""x"")"' in report


def test_format_errors_joins() -> None:
    assert format_errors([{"field": "a", "message": "m"}, {"field": "b", "message": "n"}]) == (
        "a: m; b: n"
    )
