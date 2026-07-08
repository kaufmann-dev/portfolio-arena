"""Move prompt from allocation to portfolio (one portfolio → one prompt).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("prompt_id", sa.Integer(), sa.ForeignKey("prompts.id"), nullable=True),
    )
    # Backfill each portfolio's prompt from its most recent allocation, then fall
    # back to the earliest prompt for any allocation-less portfolio.
    op.execute(
        """
        UPDATE portfolios p SET prompt_id = (
            SELECT a.prompt_id FROM allocations a
            WHERE a.portfolio_id = p.id
            ORDER BY a.effective_date DESC, a.entered_at DESC
            LIMIT 1
        )
        """
    )
    op.execute(
        "UPDATE portfolios SET prompt_id = (SELECT id FROM prompts ORDER BY id LIMIT 1) "
        "WHERE prompt_id IS NULL"
    )
    op.alter_column("portfolios", "prompt_id", nullable=False)
    op.drop_column("allocations", "prompt_id")


def downgrade() -> None:
    op.add_column(
        "allocations",
        sa.Column("prompt_id", sa.Integer(), sa.ForeignKey("prompts.id"), nullable=True),
    )
    op.drop_column("portfolios", "prompt_id")
