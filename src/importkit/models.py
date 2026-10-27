"""ORM models. Table and column names match the legacy Node schema."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    # Stored naive in UTC, matching SQLite's CURRENT_TIMESTAMP used by legacy rows.
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ImportStatus(StrEnum):
    PREVIEW = "preview"
    COMMITTED = "committed"
    REVERTED = "reverted"


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        CheckConstraint("status IN ('preview','committed','reverted')", name="ck_imports_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=ImportStatus.PREVIEW.value)
    total_rows: Mapped[int] = mapped_column(Integer)
    valid_rows: Mapped[int] = mapped_column(Integer)
    invalid_rows: Mapped[int] = mapped_column(Integer)
    created_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime)
    mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="import_", cascade="all, delete-orphan", order_by="ImportRow.row_number"
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (Index("ix_import_rows_import_status", "import_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text)
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    contact_id: Mapped[int | None] = mapped_column(Integer)

    import_: Mapped[Import] = relationship(back_populates="rows")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    # Stored lower-case; uniqueness is case-insensitive by construction.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    company: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
