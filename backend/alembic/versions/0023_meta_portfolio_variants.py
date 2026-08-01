"""Add labels for comparable meta-portfolio variants.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_portfolio_sets",
        sa.Column("variant_label", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meta_portfolio_sets", "variant_label")
