"""Add isolated arena-synthesis prompts, portfolio sets, and daily batches.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column("context_scope", sa.Text(), nullable=False, server_default="portfolio"),
    )
    op.create_check_constraint(
        "prompts_context_scope_check",
        "prompts",
        "context_scope IN ('portfolio', 'arena')",
    )

    op.create_table(
        "meta_portfolio_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("family_name", sa.Text(), nullable=False),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prompt_id",
            sa.Integer(),
            sa.ForeignKey("prompts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        "portfolios",
        sa.Column("meta_set_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "portfolios_meta_set_id_fkey",
        "portfolios",
        "meta_portfolio_sets",
        ["meta_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "portfolios_meta_set_cell_key",
        "portfolios",
        ["meta_set_id", "prompt_mode", "direction"],
    )

    op.create_table(
        "meta_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_date", sa.Date(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="waiting"),
        sa.Column(
            "source_portfolio_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "due_source_portfolio_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "target_portfolio_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "pending_target_portfolio_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_sha256", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sources_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'ready', 'insufficient', 'failed')",
            name="meta_batches_status_check",
        ),
    )

    op.add_column(
        "evaluation_runs",
        sa.Column("meta_batch_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "evaluation_runs_meta_batch_id_fkey",
        "evaluation_runs",
        "meta_batches",
        ["meta_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_evaluation_runs_meta_batch_id",
        "evaluation_runs",
        ["meta_batch_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_evaluation_runs_meta_batch_id", table_name="evaluation_runs")
    op.drop_constraint(
        "evaluation_runs_meta_batch_id_fkey",
        "evaluation_runs",
        type_="foreignkey",
    )
    op.drop_column("evaluation_runs", "meta_batch_id")
    op.drop_table("meta_batches")

    op.drop_constraint("portfolios_meta_set_cell_key", "portfolios", type_="unique")
    op.drop_constraint("portfolios_meta_set_id_fkey", "portfolios", type_="foreignkey")
    op.drop_column("portfolios", "meta_set_id")
    op.drop_table("meta_portfolio_sets")

    op.drop_constraint("prompts_context_scope_check", "prompts", type_="check")
    op.drop_column("prompts", "context_scope")
