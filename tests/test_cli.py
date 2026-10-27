from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from importkit import __main__ as cli
from importkit.config import get_settings


def test_migrate_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"sqlite:///{tmp_path / 'cli.sqlite'}"
    monkeypatch.setenv("IMPORTKIT_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cli.main(["migrate"])
    finally:
        get_settings.cache_clear()
    engine = create_engine(url)
    assert "contacts" in inspect(engine).get_table_names()
    engine.dispose()


def test_serve_command_passes_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    cli.main(["serve", "--port", "3999", "--host", "0.0.0.0"])
    assert captured["app"] == "importkit.app:create_app"
    assert captured["factory"] is True
    assert (captured["host"], captured["port"]) == ("0.0.0.0", 3999)
    assert captured["timeout_graceful_shutdown"] == 10
