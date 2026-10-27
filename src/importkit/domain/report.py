"""Error report export, hardened against CSV/formula injection."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# Spreadsheet apps evaluate cells that start with these characters as formulas.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
REPORT_HEADER = ("row_number", "name", "email", "company", "errors")


def neutralize_cell(value: object) -> str:
    """Prefix a single quote so spreadsheet software treats the cell as text."""
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_TRIGGERS):
        return "'" + text
    return text


def format_errors(errors: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(f"{e.get('field', '')}: {e.get('message', '')}" for e in errors)


def build_error_report(
    rows: Iterable[tuple[int, Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> str:
    """Render invalid rows as UTF-8-BOM, CRLF, fully quoted CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(REPORT_HEADER)
    for row_number, data, errors in rows:
        writer.writerow(
            neutralize_cell(value)
            for value in (
                row_number,
                data.get("name", ""),
                data.get("email", ""),
                data.get("company", ""),
                format_errors(errors),
            )
        )
    return "﻿" + buffer.getvalue()
