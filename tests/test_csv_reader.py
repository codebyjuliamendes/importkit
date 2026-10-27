from __future__ import annotations

import pytest

from importkit.domain.csv_reader import decode_bytes, detect_delimiter, parse_csv
from importkit.domain.errors import ImportRejectedError


def test_parses_simple_file() -> None:
    table = parse_csv("name,email\nAvery,a@x.io\n", max_rows=10)
    assert table.headers == ("name", "email")
    assert [r.values for r in table.records] == [("Avery", "a@x.io")]
    assert table.records[0].row_number == 2
    assert table.delimiter == ","


def test_strips_utf8_bom_and_handles_crlf() -> None:
    text = decode_bytes("﻿name,email\r\nAvery,a@x.io\r\n".encode())
    table = parse_csv(text, max_rows=10)
    assert table.headers == ("name", "email")
    assert table.records[0].values == ("Avery", "a@x.io")


def test_bom_in_text_input_is_stripped() -> None:
    assert parse_csv("﻿name,email\nA,b\n", max_rows=5).headers[0] == "name"


def test_quoted_fields_with_delimiters_quotes_and_newlines() -> None:
    text = 'name,email,company\n"Stone, Avery",a@x.io,"Says ""hi""\nsecond line"\n'
    table = parse_csv(text, max_rows=10)
    assert table.records[0].values == ("Stone, Avery", "a@x.io", 'Says "hi"\nsecond line')


def test_semicolon_delimiter_detected() -> None:
    text = "name;email;company\nAvery;a@x.io;Acme, Inc\nJo;j@x.io;Beta\n"
    table = parse_csv(text, max_rows=10)
    assert table.delimiter == ";"
    assert table.records[0].values == ("Avery", "a@x.io", "Acme, Inc")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a\tb\tc\n1\t2\t3\n", "\t"),
        ("a|b\n1|2\n", "|"),
        ("single\nvalue\n", ","),
        ('name,email\n"x;y",z\n', ","),
    ],
)
def test_detect_delimiter_variants(text: str, expected: str) -> None:
    assert detect_delimiter(text) == expected


def test_detect_delimiter_on_large_sample_ignores_truncated_tail() -> None:
    body = "name;email\n" + "Avery;a@x.io\n" * 3000
    assert detect_delimiter(body) == ";"


def test_blank_lines_are_skipped_but_row_numbers_match_spreadsheet() -> None:
    table = parse_csv("name,email\n\nAvery,a@x.io\n , \nJo,j@x.io", max_rows=10)
    assert [r.row_number for r in table.records] == [3, 5]


def test_unclosed_quote_is_rejected() -> None:
    with pytest.raises(ImportRejectedError) as info:
        parse_csv('name,email\n"Avery,a@x.io\n', max_rows=10)
    assert info.value.code == "malformed_csv"


@pytest.mark.parametrize("text", ["", "   \n\n"])
def test_empty_file_rejected(text: str) -> None:
    with pytest.raises(ImportRejectedError) as info:
        parse_csv(text, max_rows=10)
    assert info.value.code == "empty_file"


def test_header_only_rejected() -> None:
    with pytest.raises(ImportRejectedError) as info:
        parse_csv("name,email\n", max_rows=10)
    assert info.value.code == "missing_rows"


def test_row_limit() -> None:
    with pytest.raises(ImportRejectedError) as info:
        parse_csv("name,email\n" + "a,b\n" * 4, max_rows=3)
    assert info.value.code == "too_many_rows"
    assert info.value.status == 413


def test_decode_falls_back_to_windows_1252() -> None:
    assert decode_bytes("name\nJosé".encode("cp1252")) == "name\nJosé"


def test_decode_latin1_last_resort() -> None:
    # 0x81 is undefined in cp1252 but valid latin-1.
    assert decode_bytes(b"\x81") == "\x81"
