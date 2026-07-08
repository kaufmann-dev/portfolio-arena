"""Drop allocations.raw_response — merged into the single note field.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("allocations", "raw_response")


def downgrade() -> None:
    op.add_column(
        "allocations",
        sa.Column("raw_response", sa.Text(), nullable=False, server_default=""),
    )
