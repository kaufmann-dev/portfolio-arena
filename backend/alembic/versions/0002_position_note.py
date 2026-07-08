"""Add per-position note (admin-only agent message carried between cycles).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("positions", "note")
