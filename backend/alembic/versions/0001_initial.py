"""Baseline schema: users, settings, agents, prompts, portfolios, allocations,
positions, price_cache.

Revision ID: 0001
Revises:
Create Date: 2026-07-08
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="admin"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('admin')", name="users_role_check"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("cost_bps", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("is_benchmark", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="portfolios_status_check"),
        sa.CheckConstraint("cost_bps >= 0", name="portfolios_cost_bps_check"),
    )

    op.create_table(
        "allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_id", sa.Integer(), sa.ForeignKey("prompts.id"), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_allocations_portfolio_id", "allocations", ["portfolio_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "allocation_id",
            sa.Integer(),
            sa.ForeignKey("allocations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("weight_pct", sa.Numeric(9, 4), nullable=False),
        sa.CheckConstraint("weight_pct >= 0", name="positions_weight_pct_check"),
        sa.CheckConstraint("instrument IN ('equity', 'cash')", name="positions_instrument_check"),
        sa.UniqueConstraint("allocation_id", "symbol", name="positions_allocation_id_symbol_key"),
    )
    op.create_index("idx_positions_allocation_id", "positions", ["allocation_id"])

    op.create_table(
        "price_cache",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("series", JSONB(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_price_cache_fetched_at", "price_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_table("price_cache")
    op.drop_table("positions")
    op.drop_table("allocations")
    op.drop_table("portfolios")
    op.drop_table("prompts")
    op.drop_table("agents")
    op.drop_table("settings")
    op.drop_table("users")
