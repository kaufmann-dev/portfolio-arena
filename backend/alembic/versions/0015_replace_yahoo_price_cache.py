"""Clear Yahoo prices and canonicalize class-share symbols for Massive.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Yahoo spells US share classes with a hyphen (BRK-B); Massive uses the
    # exchange spelling with a dot (BRK.B). This is a one-time data migration,
    # not a runtime alias or dual-provider compatibility path.
    op.execute(
        """
        UPDATE positions
        SET symbol = left(symbol, length(symbol) - 2) || '.' || right(symbol, 1)
        WHERE symbol ~ '^[A-Z0-9]+-[A-Z]$'
        """
    )
    op.execute("DELETE FROM price_cache")
    op.add_column("price_cache", sa.Column("end_date", sa.Date(), nullable=False))


def downgrade() -> None:
    # Provider provenance and the prior spelling cannot be reconstructed
    # safely, so the downgrade leaves canonical symbols and clears prices.
    op.execute("DELETE FROM price_cache")
    op.drop_column("price_cache", "end_date")
