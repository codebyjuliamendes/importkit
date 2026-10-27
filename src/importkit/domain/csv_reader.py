"""Tolerant CSV decoding and parsing.

Handles UTF-8 (with or without BOM) and legacy Windows-1252 exports, CRLF/LF/CR
line endings, quoted fields with embedded delimiters, quotes and newlines, and
comma, semicolon, tab or pipe delimiters.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from importkit.domain.errors import ImportRejectedError

CANDIDATE_DELIMITERS = (",", ";", "\t", "|")
_SNIFF_SAMPLE_CHARS = 16_384
_SCORE_RECORDS = 25


@dataclass(frozen=True, slots=True)
class CsvRecord:
    """One data record. ``row_number`` is the spreadsheet row (header is row 1)."""

    row_number: int
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CsvTable:
    headers: tuple[str, ...]
    records: tuple[CsvRecord, ...]
    delimiter: str


def decode_bytes(raw: bytes) -> str:
    """Decode an uploaded file, preferring UTF-8 and stripping a BOM."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        # latin-1 maps every byte, so this never fails.
        return raw.decode("latin-1")


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith("﻿") else text


def _consistency_score(sample: str, delimiter: str) -> tuple[int, int]:
    """Score a delimiter by how many leading records agree with the header width."""
    reader = csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter)
    widths: list[int] = []
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            widths.append(len(row))
            if len(widths) >= _SCORE_RECORDS:
                break
    except csv.Error:
        return (0, 0)
    if not widths or widths[0] < 2:
        return (0, 0)
    header_width = widths[0]
    agreeing = sum(1 for width in widths if width == header_width)
    return (agreeing, header_width)


def detect_delimiter(text: str) -> str:
    """Pick the delimiter, trusting ``csv.Sniffer`` only when it is self-consistent."""
    sample = text[:_SNIFF_SAMPLE_CHARS]
    # Drop a possibly truncated trailing line so it does not skew the score.
    if len(text) > _SNIFF_SAMPLE_CHARS and "\n" in sample:
        sample = sample[: sample.rfind("\n")]
    scores = {d: _consistency_score(sample, d) for d in CANDIDATE_DELIMITERS}
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS)).delimiter
    except csv.Error:
        sniffed = None
    best = max(CANDIDATE_DELIMITERS, key=lambda d: scores[d])
    if sniffed in scores and scores[sniffed] >= scores[best] and scores[sniffed][1] >= 2:
        return sniffed
    if scores[best][1] >= 2:
        return best
    return ","


def parse_csv(text: str, *, max_rows: int) -> CsvTable:
    """Parse CSV text into a header and data records, skipping blank lines."""
    text = _strip_bom(text)
    if not text.strip():
        raise ImportRejectedError("empty_file", "Upload a non-empty CSV file.")
    delimiter = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    headers: tuple[str, ...] | None = None
    records: list[CsvRecord] = []
    ordinal = 0
    try:
        for row in reader:
            ordinal += 1
            if not any(cell.strip() for cell in row):
                continue
            if headers is None:
                headers = tuple(row)
                continue
            if len(records) >= max_rows:
                raise ImportRejectedError(
                    "too_many_rows",
                    f"The CSV has more than {max_rows:,} data rows. Split it into smaller files.",
                    status=413,
                )
            records.append(CsvRecord(row_number=ordinal, values=tuple(row)))
    except csv.Error as exc:
        raise ImportRejectedError(
            "malformed_csv",
            f"The CSV is malformed near line {reader.line_num}: a quoted field is not closed "
            "or is followed by unexpected text.",
        ) from exc
    if headers is None or not records:
        raise ImportRejectedError(
            "missing_rows", "The CSV needs a header and at least one data row."
        )
    return CsvTable(headers=headers, records=tuple(records), delimiter=delimiter)
