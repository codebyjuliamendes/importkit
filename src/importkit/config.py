"""Validated runtime configuration (environment variables and optional ``.env``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_SQLITE_PATH = Path("data") / "importkit.sqlite"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IMPORTKIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(
        default=3205, ge=1, le=65535, validation_alias=AliasChoices("IMPORTKIT_PORT", "PORT")
    )
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("IMPORTKIT_DATABASE_URL", "DATABASE_URL"),
        description="SQLAlchemy URL. Empty means SQLite at DATABASE_PATH or ./data.",
    )
    # Legacy variable from the Node version, still honoured.
    database_path: Path | None = Field(
        default=None, validation_alias=AliasChoices("IMPORTKIT_DATABASE_PATH", "DATABASE_PATH")
    )
    auto_migrate: bool = False

    max_upload_bytes: int = Field(default=800_000, ge=1_000, le=50_000_000)
    max_rows: int = Field(default=10_000, ge=1, le=1_000_000)
    allow_test_email_domains: bool = False

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    frame_ancestors: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["'self'"])
    rate_limit_writes: int = Field(default=30, ge=0, description="Writes per window; 0 = off.")
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    enable_docs: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    static_dir: Path = Path(__file__).resolve().parent.parent.parent / "public"

    @field_validator("cors_origins", "frame_ancestors", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError(f"CORS origin must be an explicit http(s) origin: {origin!r}")
        return [origin.rstrip("/") for origin in value]

    @field_validator("frame_ancestors")
    @classmethod
    def _safe_ancestors(cls, value: list[str]) -> list[str]:
        for source in value:
            if any(ch in source for ch in ";,\r\n") or source == "*":
                raise ValueError(f"Invalid frame-ancestors source: {source!r}")
        return value or ["'none'"]

    @model_validator(mode="after")
    def _resolve_database(self) -> Settings:
        if not self.database_url:
            path = self.database_path or DEFAULT_SQLITE_PATH
            self.database_url = f"sqlite:///{path}"
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def max_request_bytes(self) -> int:
        # JSON escaping and multipart framing can roughly double the CSV size.
        return self.max_upload_bytes * 2 + 64 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
