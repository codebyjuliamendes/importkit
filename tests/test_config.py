from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from importkit.config import Settings


def make(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg, arg-type]


def test_defaults_use_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("IMPORTKIT_DATABASE_URL", "DATABASE_URL", "DATABASE_PATH", "PORT"):
        monkeypatch.delenv(var, raising=False)
    settings = make()
    assert settings.database_url == f"sqlite:///{Path('data') / 'importkit.sqlite'}"
    assert settings.is_sqlite
    assert settings.port == 3205


def test_legacy_env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("IMPORTKIT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "x.sqlite"))
    monkeypatch.setenv("PORT", "4000")
    settings = make()
    assert settings.database_url.endswith("x.sqlite")
    assert settings.port == 4000


def test_lists_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORTKIT_CORS_ORIGINS", "https://a.example, https://b.example/")
    monkeypatch.setenv("IMPORTKIT_FRAME_ANCESTORS", "'self',https://a.example")
    settings = make()
    assert settings.cors_origins == ["https://a.example", "https://b.example"]
    assert settings.frame_ancestors == ["'self'", "https://a.example"]


@pytest.mark.parametrize("origin", ["*", "example.com"])
def test_rejects_unsafe_cors(origin: str) -> None:
    with pytest.raises(ValidationError):
        make(cors_origins=[origin])


def test_rejects_header_injection_in_frame_ancestors() -> None:
    with pytest.raises(ValidationError):
        make(frame_ancestors=["'self'; script-src *"])


def test_empty_frame_ancestors_means_none() -> None:
    assert make(frame_ancestors=[]).frame_ancestors == ["'none'"]


def test_request_budget_exceeds_upload_limit() -> None:
    settings = make(max_upload_bytes=1_000)
    assert settings.max_request_bytes > settings.max_upload_bytes
