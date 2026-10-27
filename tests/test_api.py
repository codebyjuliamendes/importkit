from __future__ import annotations

import csv
import io
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import preview

SAMPLE = (
    "name,email,company\n"
    "Avery Stone,avery@example.com,Northstar Labs\n"
    "Jordan Lee,jordan@example.com,Fieldnote\n"
    "Sam Rivera,not-an-email,Studio North\n"
    "Taylor Morgan,avery@example.com,Duplicate row"
)


def test_maps_custom_headers_commits_valid_rows_and_exports_safe_errors(
    client: TestClient,
) -> None:
    """Port of the original Node integration test."""
    csv_text = (
        "Full Name,E-mail,Organization\n"
        "Avery Stone,avery@northstar.test,Northstar\n"
        "Taylor Stone,invalid-address,=HYPERLINK"
    )
    body = preview(
        client,
        csv_text,
        filename="external-contacts.csv",
        mapping={"name": "Full Name", "email": "E-mail", "company": "Organization"},
    )
    assert body["validRows"] == 1
    assert body["invalidRows"] == 1
    assert body["rows"][0]["data"]["company"] == "Northstar"

    details = client.get(f"/api/imports/{body['id']}").json()
    assert details["mapping"]["email"] == "e_mail"

    committed = client.post(f"/api/imports/{body['id']}/commit", json={}).json()
    assert committed["created"] == 1

    contacts = client.get("/api/contacts").json()
    assert any(c["name"] == "Avery Stone" for c in contacts)
    assert not any(c["name"] == "Taylor Stone" for c in contacts)

    report = client.get(f"/api/imports/{body['id']}/errors.csv")
    assert report.status_code == 200
    assert "text/csv" in report.headers["content-type"]
    assert '"\'=HYPERLINK"' in report.text
    assert 'filename="import-errors-' in report.headers["content-disposition"]


def test_sample_flow_dashboard_and_revert(client: TestClient) -> None:
    body = preview(client, SAMPLE, filename="sample-contacts.csv")
    assert (body["totalRows"], body["validRows"], body["invalidRows"]) == (4, 2, 2)
    assert body["status"] == "preview"
    assert body["delimiter"] == ","
    invalid = {r["rowNumber"]: r["errors"][0]["code"] for r in body["rows"] if r["errors"]}
    assert invalid == {4: "email_invalid", 5: "email_duplicate_in_file"}

    dash = client.get("/api/dashboard").json()
    assert (dash["contacts"], dash["imports"], dash["pending"], dash["rejected"]) == (0, 1, 1, 2)
    assert dash["latest"][0]["filename"] == "sample-contacts.csv"
    assert dash["latest"][0]["createdAt"].endswith("Z")

    assert client.post(f"/api/imports/{body['id']}/commit").json()["created"] == 2
    assert client.get("/api/dashboard").json()["contacts"] == 2

    reverted = client.post(f"/api/imports/{body['id']}/revert", json={})
    assert reverted.status_code == 200
    assert reverted.json() == {"ok": True, "removed": 2, "replayed": False}
    assert client.get("/api/contacts").json() == []
    detail = client.get(f"/api/imports/{body['id']}").json()
    assert detail["status"] == "reverted"
    assert detail["revertedAt"].endswith("Z")
    assert client.get("/api/dashboard").json()["rejected"] == 0


def test_commit_is_idempotent(client: TestClient) -> None:
    body = preview(client, SAMPLE)
    first = client.post(f"/api/imports/{body['id']}/commit").json()
    second = client.post(f"/api/imports/{body['id']}/commit").json()
    assert first == {"ok": True, "created": 2, "replayed": False}
    assert second == {"ok": True, "created": 2, "replayed": True}
    assert len(client.get("/api/contacts").json()) == 2


def test_revert_is_idempotent_and_reverted_batch_cannot_be_recommitted(client: TestClient) -> None:
    body = preview(client, SAMPLE)
    client.post(f"/api/imports/{body['id']}/commit")
    client.post(f"/api/imports/{body['id']}/revert")
    again = client.post(f"/api/imports/{body['id']}/revert")
    assert again.json() == {"ok": True, "removed": 0, "replayed": True}
    recommit = client.post(f"/api/imports/{body['id']}/commit")
    assert recommit.status_code == 409
    assert recommit.json()["error"] == "Only a preview can be committed."


def test_revert_requires_committed(client: TestClient) -> None:
    body = preview(client, SAMPLE)
    response = client.post(f"/api/imports/{body['id']}/revert")
    assert response.status_code == 409
    assert response.json()["error"] == "Only a committed import can be reverted."


def test_revert_only_touches_its_own_batch(client: TestClient) -> None:
    first = preview(client, "name,email\nAvery,avery@example.com")
    client.post(f"/api/imports/{first['id']}/commit")
    second = preview(client, "name,email\nJordan,jordan@example.com")
    client.post(f"/api/imports/{second['id']}/commit")
    client.post(f"/api/imports/{second['id']}/revert")
    assert [c["email"] for c in client.get("/api/contacts").json()] == ["avery@example.com"]


def test_existing_contacts_are_flagged_in_later_previews(client: TestClient) -> None:
    first = preview(client, "name,email\nAvery,avery@example.com")
    client.post(f"/api/imports/{first['id']}/commit")
    later = preview(client, "name,email\nAvery Again,AVERY@example.com")
    assert later["rows"][0]["errors"][0]["code"] == "email_exists"


def test_commit_detects_emails_taken_after_preview(client: TestClient) -> None:
    a = preview(client, "name,email\nAvery,avery@example.com\nJordan,jordan@example.com")
    b = preview(client, "name,email\nAvery B,avery@example.com")
    assert client.post(f"/api/imports/{b['id']}/commit").json()["created"] == 1
    result = client.post(f"/api/imports/{a['id']}/commit").json()
    assert result["created"] == 1
    detail = client.get(f"/api/imports/{a['id']}").json()
    assert (detail["validRows"], detail["invalidRows"], detail["createdCount"]) == (1, 1, 1)
    conflict = next(r for r in detail["rows"] if r["status"] == "invalid")
    assert conflict["errors"][0]["code"] == "email_conflict"


def test_semicolon_crlf_bom_and_quoted_newlines(client: TestClient) -> None:
    csv_text = '﻿Nome;E-mail;Empresa\r\n"Stone; Avery";avery@example.com;"Line\r\nTwo"\r\n'
    body = preview(client, csv_text)
    assert body["delimiter"] == ";"
    assert body["mapping"] == {"name": "nome", "email": "e_mail", "company": "empresa"}
    assert body["rows"][0]["data"] == {
        "name": "Stone; Avery",
        "email": "avery@example.com",
        "company": "Line Two",
    }


def test_multipart_upload_with_windows_1252(client: TestClient) -> None:
    raw = "Full Name,Email\nJosé Núñez,jose@example.com\n".encode("cp1252")
    response = client.post(
        "/api/imports/preview",
        files={"file": ("legacy.csv", raw, "text/csv")},
        data={"nameColumn": "Full Name", "emailColumn": "Email"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "legacy.csv"
    assert body["rows"][0]["data"]["name"] == "José Núñez"


def test_multipart_requires_file(client: TestClient) -> None:
    response = client.post(
        "/api/imports/preview", data={"filename": "x.csv"}, files={"x": ("a", b"")}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "missing_file"


def test_inspect_suggests_mapping_without_storing(client: TestClient) -> None:
    response = client.post(
        "/api/imports/inspect", json={"csv": "Full Name,E-mail,Org\nAvery,a@example.com,X\n"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["headers"][0] == {"label": "Full Name", "key": "full_name"}
    assert body["suggestedMapping"] == {"name": "full_name", "email": "e_mail", "company": "org"}
    assert body["totalRows"] == 1
    assert body["sample"] == [["Avery", "a@example.com", "X"]]
    assert client.get("/api/dashboard").json()["imports"] == 0


def test_inspect_multipart_and_errors(client: TestClient) -> None:
    ok = client.post("/api/imports/inspect", files={"file": ("a.csv", b"name;email\nA;b\n")})
    assert ok.json()["delimiter"] == ";"
    bad = client.post("/api/imports/inspect", json={"nope": 1})
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("payload", "status", "message"),
    [
        ({"csv": ""}, 400, "Upload a non-empty CSV file."),
        ({"csv": "name,email\n"}, 400, "The CSV needs a header and at least one data row."),
        ({"csv": "Email,email\na,b"}, 400, "The CSV contains duplicate column names."),
        (
            {"csv": "first,last\na,b"},
            400,
            "Choose a name and email column that exist in the CSV. Company is optional.",
        ),
        (
            {"csv": "name,email\na,b", "mapping": {"company": "Org"}},
            400,
            "The selected company column does not exist in the CSV.",
        ),
        ({"csv": 'name,email\n"a,b'}, 400, None),
        ({"filename": "x"}, 400, "Send JSON with a 'csv' string, or upload a file."),
    ],
)
def test_preview_rejections(
    client: TestClient, payload: dict[str, object], status: int, message: str | None
) -> None:
    response = client.post("/api/imports/preview", json=payload)
    assert response.status_code == status
    body = response.json()
    assert isinstance(body["error"], str)
    assert body["code"]
    if message:
        assert body["error"] == message


def test_column_count_mismatch_is_reported_per_row(client: TestClient) -> None:
    body = preview(client, "name,email,company\nAvery,a@example.com\nJo,j@example.com,X")
    assert body["rows"][0]["errors"][0]["code"] == "column_count_mismatch"
    assert body["validRows"] == 1


def test_file_size_and_row_limits(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_upload_bytes=1_000, max_rows=2)
    big = client.post("/api/imports/preview", json={"csv": "name,email\n" + "a" * 2_000})
    assert big.status_code == 413
    assert big.json()["code"] == "file_too_large"
    many = client.post(
        "/api/imports/preview", json={"csv": "name,email\n" + "Avery,a@example.com\n" * 3}
    )
    assert many.status_code == 413
    assert many.json()["code"] == "too_many_rows"


def test_filename_is_sanitised(client: TestClient) -> None:
    body = preview(client, SAMPLE, filename="../../etc/\x00passwd.csv")
    assert body["filename"] == "passwd.csv"
    assert preview(client, SAMPLE, filename="   ")["filename"] == "contacts.csv"
    assert len(preview(client, SAMPLE, filename="a" * 300)["filename"]) == 100


def test_not_found_and_invalid_ids(client: TestClient) -> None:
    for path in ("/api/imports/999", "/api/imports/999/errors.csv"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"] == "Import not found."
    for action in ("commit", "revert"):
        assert client.post(f"/api/imports/999/{action}").status_code == 404
    invalid = client.get("/api/imports/abc")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_request"


def test_unknown_api_route_and_static_404_are_json(client: TestClient) -> None:
    api = client.get("/api/unknown")
    assert api.status_code == 404
    assert api.json() == {"error": "API route not found.", "code": "not_found"}
    static = client.get("/missing.png")
    assert static.status_code == 404
    assert static.headers["content-type"].startswith("application/json")


def test_contacts_pagination(client: TestClient) -> None:
    body = preview(client, SAMPLE)
    client.post(f"/api/imports/{body['id']}/commit")
    assert len(client.get("/api/contacts?limit=1").json()) == 1
    assert client.get("/api/contacts?limit=1&offset=1").json()[0]["email"] == "avery@example.com"
    assert client.get("/api/contacts?limit=0").status_code == 400


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_database_failure(client: TestClient) -> None:
    client.app.state.engine.dispose()  # type: ignore[attr-defined]

    class Broken:
        def __enter__(self) -> Broken:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, *args: object) -> None:
            raise RuntimeError("down")

    client.app.state.session_factory = Broken  # type: ignore[attr-defined]
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_frontend_is_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert client.get("/app.js").status_code == 200
    assert client.get("/mark-ik.svg").status_code == 200


def test_openapi_docs_available(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/imports/preview" in schema["paths"]
    assert client.get("/docs").status_code == 200


def test_docs_can_be_disabled(make_client: Callable[..., TestClient]) -> None:
    client = make_client(enable_docs=False)
    assert client.get("/docs").status_code == 404


def test_error_report_round_trips_through_csv_reader(client: TestClient) -> None:
    body = preview(client, 'name,email\n"@SUM(1)",bad\n"-2+3",worse\n')
    report = client.get(f"/api/imports/{body['id']}/errors.csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(report, newline="")))
    assert [r[1] for r in rows[1:]] == ["'@SUM(1)", "'-2+3"]


def test_unmapped_company_is_respected(client: TestClient) -> None:
    body = preview(
        client,
        "name,email,company\nAvery,a@example.com,Acme",
        mapping={"name": "name", "email": "email", "company": ""},
    )
    assert body["mapping"]["company"] == ""
    assert body["rows"][0]["data"]["company"] == ""
