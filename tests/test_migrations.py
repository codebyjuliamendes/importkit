from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from importkit import migrate
from importkit.app import create_app
from importkit.config import Settings
from importkit.models import Base

# Exact DDL used by the original Node server.
LEGACY_DDL = """
CREATE TABLE imports(id INTEGER PRIMARY KEY,filename TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('preview','committed','reverted')),total_rows INTEGER NOT NULL,valid_rows INTEGER NOT NULL,invalid_rows INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,committed_at TEXT,reverted_at TEXT,mapping_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE import_rows(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,row_number INTEGER NOT NULL,data_json TEXT NOT NULL,status TEXT NOT NULL,errors_json TEXT NOT NULL,contact_id INTEGER);
CREATE TABLE contacts(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL REFERENCES imports(id),name TEXT NOT NULL,email TEXT NOT NULL UNIQUE COLLATE NOCASE,company TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def test_upgrade_creates_schema_matching_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'fresh.sqlite'}"
    migrate.upgrade(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_to_base_and_back(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'cycle.sqlite'}"
    migrate.upgrade(url)
    migrate.downgrade(url, "0001_baseline")
    engine = create_engine(url)
    assert "created_count" not in {c["name"] for c in inspect(engine).get_columns("imports")}
    migrate.downgrade(url, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()
    migrate.upgrade(url)


def test_adopts_legacy_node_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.execute(
        "INSERT INTO imports(id,filename,status,total_rows,valid_rows,invalid_rows,"
        "created_at,committed_at,mapping_json) VALUES "
        "(1,'old.csv','committed',2,1,1,'2025-01-02 03:04:05','2025-01-02 03:05:00',"
        '\'{"name":"name","email":"email","company":"company"}\')'
    )
    conn.execute(
        "INSERT INTO import_rows(import_id,row_number,data_json,status,errors_json,contact_id) "
        'VALUES (1,2,\'{"name":"Avery","email":"avery@example.com","company":""}\','
        "'valid','[]',1),"
        '(1,3,\'{"name":"T","email":"bad","company":"=X"}\',\'invalid\','
        '\'[{"field":"email","message":"Enter a valid email address."}]\',NULL)'
    )
    conn.execute(
        "INSERT INTO contacts(id,import_id,name,email,company,created_at) "
        "VALUES (1,1,'Avery','avery@example.com','','2025-01-02 03:05:00')"
    )
    conn.commit()
    conn.close()

    url = f"sqlite:///{path}"
    migrate.upgrade(url)
    settings = Settings(_env_file=None, database_url=url, log_level="WARNING")  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        detail = client.get("/api/imports/1").json()
        assert detail["createdAt"] == "2025-01-02T03:04:05Z"
        assert detail["createdCount"] == 1
        assert detail["rows"][1]["errors"][0] == {
            "row": None,
            "field": "email",
            "column": None,
            "code": None,
            "message": "Enter a valid email address.",
        }
        assert client.post("/api/imports/1/commit").json()["replayed"] is True
        report = client.get("/api/imports/1/errors.csv").text
        assert '"\'=X"' in report
        later = client.post(
            "/api/imports/preview", json={"csv": "name,email\nAvery,AVERY@example.com"}
        ).json()
        assert later["rows"][0]["errors"][0]["code"] == "email_exists"
        assert client.post("/api/imports/1/revert").json()["removed"] == 1
