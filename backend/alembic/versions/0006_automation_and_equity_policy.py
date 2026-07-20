"""Automated evaluation runs and equity-only prompt policies.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


_WORKFLOW = (
    "Portfolio to manage: <PORTFOLIO_SLUG_OR_ID>\n\n"
    "First call the Portfolio Arena MCP `get_portfolio` with this value. Use its current holdings, "
    "allocation history, notes, performance, and effective date. Manage this portfolio rather than "
    "rebuilding a new one: every holding must re-earn its place, but prior ownership alone is never "
    "a reason to retain it. After deciding, call `create_allocation` with the returned portfolio ID, "
    "target positions, and a brief rebalance note.\n\n"
)

_OLD_POLICY = (
    "You are a US equity portfolio manager. Use current S&P 500 common stocks and CASH:USD only, "
    "aiming to outperform SPY. Use current, decision-relevant evidence. Weights must total 100%; "
    "every position, including cash, must be 10–25%. Do not mirror SPY or fund a generic holding "
    "without a distinct, falsifiable company-specific thesis."
)

_NEW_POLICY = (
    "You are a US equity portfolio manager aiming to outperform SPY. Use current, decision-relevant "
    "evidence and focus on current S&P 500 common stocks and USD-denominated ETFs. Do not mirror SPY "
    "or fund a generic holding without a distinct, falsifiable company-specific thesis."
)


def upgrade() -> None:
    connection = op.get_bind()
    cash_count = connection.scalar(sa.text("SELECT count(*) FROM positions WHERE instrument = 'cash'"))
    if cash_count:
        raise RuntimeError(
            f"Cannot remove cash support while {cash_count} cash position(s) exist; replace them first."
        )

    op.add_column("prompts", sa.Column("min_position_weight_pct", sa.Numeric(9, 4), nullable=True))
    op.add_column("prompts", sa.Column("max_position_weight_pct", sa.Numeric(9, 4), nullable=True))
    op.execute(
        "UPDATE prompts SET min_position_weight_pct = CASE WHEN slug = 'buy-and-hold' THEN 100 ELSE 10 END, "
        "max_position_weight_pct = CASE WHEN slug = 'buy-and-hold' THEN 100 ELSE 25 END"
    )
    op.alter_column("prompts", "min_position_weight_pct", nullable=False)
    op.alter_column("prompts", "max_position_weight_pct", nullable=False)
    op.create_check_constraint(
        "prompts_position_weights_check",
        "prompts",
        "min_position_weight_pct > 0 AND max_position_weight_pct <= 100 "
        "AND min_position_weight_pct <= max_position_weight_pct",
    )

    connection.execute(
        sa.text(
            "UPDATE prompts SET text = replace(replace(text, :workflow, ''), :old_policy, :new_policy) "
            "WHERE slug <> 'buy-and-hold'"
        ),
        {"workflow": _WORKFLOW, "old_policy": _OLD_POLICY, "new_policy": _NEW_POLICY},
    )

    op.drop_constraint("positions_instrument_check", "positions", type_="check")
    op.drop_column("positions", "instrument")

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("codex_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "allocation_id",
            sa.Integer(),
            sa.ForeignKey("allocations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')",
            name="evaluation_runs_status_check",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="evaluation_runs_attempt_count_check"),
        sa.UniqueConstraint("portfolio_id", "scheduled_for", name="evaluation_runs_portfolio_session_key"),
    )
    op.create_index("idx_evaluation_runs_scheduled_id", "evaluation_runs", ["scheduled_for", "id"])


def downgrade() -> None:
    op.drop_index("idx_evaluation_runs_scheduled_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.add_column("positions", sa.Column("instrument", sa.Text(), nullable=False, server_default="equity"))
    op.create_check_constraint("positions_instrument_check", "positions", "instrument IN ('equity', 'cash')")
    op.drop_constraint("prompts_position_weights_check", "prompts", type_="check")
    op.drop_column("prompts", "max_position_weight_pct")
    op.drop_column("prompts", "min_position_weight_pct")
