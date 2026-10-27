"""Baseline schema, adopting databases created by the legacy Node service.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "imports" not in tables:
        op.create_table(
            "imports",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("filename", sa.Text, nullable=False),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("total_rows", sa.Integer, nullable=False),
            sa.Column("valid_rows", sa.Integer, nullable=False),
            sa.Column("invalid_rows", sa.Integer, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("committed_at", sa.DateTime),
            sa.Column("reverted_at", sa.DateTime),
            sa.Column("mapping_json", sa.JSON, nullable=False, server_default="{}"),
            sa.CheckConstraint(
                "status IN ('preview','committed','reverted')", name="ck_imports_status"
            ),
        )
    elif "mapping_json" not in {c["name"] for c in inspector.get_columns("imports")}:
        # Very old Node databases predate the mapping column.
        op.add_column(
            "imports", sa.Column("mapping_json", sa.Text, nullable=False, server_default="{}")
        )

    if "import_rows" not in tables:
        op.create_table(
            "import_rows",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "import_id",
                sa.Integer,
                sa.ForeignKey("imports.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("row_number", sa.Integer, nullable=False),
            sa.Column("data_json", sa.JSON, nullable=False),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("errors_json", sa.JSON, nullable=False),
            sa.Column("contact_id", sa.Integer),
        )

    if "contacts" not in tables:
        op.create_table(
            "contacts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("import_id", sa.Integer, sa.ForeignKey("imports.id"), nullable=False),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("email", sa.String(320), nullable=False, unique=True),
            sa.Column("company", sa.Text, nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
        )


def downgrade() -> None:
    op.drop_table("contacts")
    op.drop_table("import_rows")
    op.drop_table("imports")
