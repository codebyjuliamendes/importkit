"""Track created contacts per batch for idempotent commits; add lookup indexes.

Revision ID: 0002_idempotent_commit
Revises: 0001_baseline
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_idempotent_commit"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "imports",
        sa.Column("created_count", sa.Integer, nullable=False, server_default="0"),
    )
    # Backfill from rows linked to a contact by the legacy commit path.
    op.execute(
        "UPDATE imports SET created_count = ("
        "SELECT COUNT(*) FROM import_rows r "
        "WHERE r.import_id = imports.id AND r.contact_id IS NOT NULL)"
    )
    op.create_index("ix_contacts_import_id", "contacts", ["import_id"])
    op.create_index("ix_import_rows_import_status", "import_rows", ["import_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_import_rows_import_status", table_name="import_rows")
    op.drop_index("ix_contacts_import_id", table_name="contacts")
    with op.batch_alter_table("imports") as batch:
        batch.drop_column("created_count")
