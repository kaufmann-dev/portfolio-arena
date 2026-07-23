"""Integrated evaluator configuration, queue, and runtime status.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-23
"""

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


_LEGACY_ASSIGNMENTS = {
    "gpt-sol-barebones": "gpt-5.6-sol",
    "gpt-sol-secular-change": "gpt-5.6-sol",
    "gpt-5-6-terra-weekly-barebones": "gpt-5.6-terra",
    "gpt-terra-secular-change": "gpt-5.6-terra",
    "gpt-luna-barebones": "gpt-5.6-luna",
    "gpt-luna-secular-change": "gpt-5.6-luna",
}


def upgrade() -> None:
    op.create_table(
        "evaluator_settings",
        sa.Column("id", sa.SmallInteger(), server_default="1", primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), server_default="5", nullable=False),
        sa.Column("poll_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("attempt_timeout_seconds", sa.Integer(), server_default="1500", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
        sa.Column("reasoning_effort", sa.Text(), server_default="xhigh", nullable=False),
        sa.Column("service_tier", sa.Text(), server_default="fast", nullable=False),
        sa.Column("start_before_close_minutes", sa.Integer(), server_default="90", nullable=False),
        sa.Column("cutoff_before_close_minutes", sa.Integer(), server_default="10", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="evaluator_settings_singleton_check"),
        sa.CheckConstraint(
            "max_concurrency BETWEEN 1 AND 20",
            name="evaluator_settings_concurrency_check",
        ),
        sa.CheckConstraint("poll_seconds BETWEEN 10 AND 300", name="evaluator_settings_poll_check"),
        sa.CheckConstraint(
            "attempt_timeout_seconds BETWEEN 60 AND 1500",
            name="evaluator_settings_timeout_check",
        ),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 5", name="evaluator_settings_attempts_check"),
        sa.CheckConstraint(
            "reasoning_effort IN ('low', 'medium', 'high', 'xhigh')",
            name="evaluator_settings_reasoning_check",
        ),
        sa.CheckConstraint(
            "service_tier IN ('standard', 'fast')",
            name="evaluator_settings_service_tier_check",
        ),
        sa.CheckConstraint(
            "start_before_close_minutes BETWEEN 15 AND 240",
            name="evaluator_settings_start_check",
        ),
        sa.CheckConstraint(
            "cutoff_before_close_minutes BETWEEN 0 AND 60",
            name="evaluator_settings_cutoff_check",
        ),
        sa.CheckConstraint(
            "start_before_close_minutes > cutoff_before_close_minutes",
            name="evaluator_settings_window_check",
        ),
    )
    op.execute("INSERT INTO evaluator_settings (id) VALUES (1)")

    op.create_table(
        "portfolio_evaluator_configs",
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "weekdays",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    connection = op.get_bind()
    for slug, model in _LEGACY_ASSIGNMENTS.items():
        connection.execute(
            sa.text(
                "INSERT INTO portfolio_evaluator_configs (portfolio_id, enabled, model, weekdays) "
                "SELECT id, true, :model, CAST(:weekdays AS jsonb) FROM portfolios WHERE slug = :slug "
                "ON CONFLICT (portfolio_id) DO NOTHING"
            ),
            {"slug": slug, "model": model, "weekdays": json.dumps([0, 1, 2, 3, 4])},
        )

    op.create_table(
        "evaluator_instances",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("codex_version", sa.Text(), nullable=True),
        sa.Column("authenticated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("active_run_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_evaluator_instances_heartbeat",
        "evaluator_instances",
        ["last_heartbeat_at"],
    )

    op.drop_constraint("evaluation_runs_status_check", "evaluation_runs", type_="check")
    op.drop_constraint("evaluation_runs_portfolio_session_key", "evaluation_runs", type_="unique")
    op.alter_column("evaluation_runs", "scheduled_for", nullable=True)
    op.alter_column("evaluation_runs", "codex_version", nullable=True)
    op.add_column(
        "evaluation_runs",
        sa.Column("trigger_kind", sa.Text(), server_default="scheduled", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "retry_of_run_id",
            sa.Integer(),
            sa.ForeignKey("evaluation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("reasoning_effort", sa.Text(), server_default="xhigh", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("service_tier", sa.Text(), server_default="fast", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("timeout_seconds", sa.Integer(), server_default="1500", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("evaluation_runs", sa.Column("worker_id", sa.Text(), nullable=True))
    op.execute(
        "UPDATE evaluation_runs SET status = 'failed', lease_expires_at = NULL, "
        "finished_at = now(), error = COALESCE(error, 'Interrupted by integrated evaluator migration.') "
        "WHERE status = 'running'"
    )
    op.create_check_constraint(
        "evaluation_runs_status_check",
        "evaluation_runs",
        "status IN ('queued', 'running', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'skipped')",
    )
    op.create_check_constraint(
        "evaluation_runs_trigger_kind_check",
        "evaluation_runs",
        "trigger_kind IN ('scheduled', 'manual', 'retry')",
    )
    op.create_check_constraint(
        "evaluation_runs_max_attempts_check",
        "evaluation_runs",
        "max_attempts BETWEEN 1 AND 5",
    )
    op.create_check_constraint(
        "evaluation_runs_timeout_check",
        "evaluation_runs",
        "timeout_seconds BETWEEN 60 AND 1500",
    )
    op.create_index(
        "evaluation_runs_portfolio_session_key",
        "evaluation_runs",
        ["portfolio_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("trigger_kind = 'scheduled' AND scheduled_for IS NOT NULL"),
    )
    op.create_index(
        "evaluation_runs_portfolio_active_key",
        "evaluation_runs",
        ["portfolio_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancel_requested')"),
    )


def downgrade() -> None:
    op.drop_index("evaluation_runs_portfolio_active_key", table_name="evaluation_runs")
    op.drop_index("evaluation_runs_portfolio_session_key", table_name="evaluation_runs")
    op.drop_constraint("evaluation_runs_timeout_check", "evaluation_runs", type_="check")
    op.drop_constraint("evaluation_runs_max_attempts_check", "evaluation_runs", type_="check")
    op.drop_constraint("evaluation_runs_trigger_kind_check", "evaluation_runs", type_="check")
    op.drop_constraint("evaluation_runs_status_check", "evaluation_runs", type_="check")
    op.execute(
        "DELETE FROM evaluation_runs WHERE scheduled_for IS NULL OR "
        "status IN ('queued', 'cancel_requested', 'cancelled')"
    )
    op.execute("UPDATE evaluation_runs SET codex_version = COALESCE(codex_version, 'unknown')")
    op.alter_column("evaluation_runs", "codex_version", nullable=False)
    op.alter_column("evaluation_runs", "scheduled_for", nullable=False)
    op.drop_column("evaluation_runs", "worker_id")
    op.drop_column("evaluation_runs", "deadline_at")
    op.drop_column("evaluation_runs", "max_attempts")
    op.drop_column("evaluation_runs", "timeout_seconds")
    op.drop_column("evaluation_runs", "service_tier")
    op.drop_column("evaluation_runs", "reasoning_effort")
    op.drop_column("evaluation_runs", "retry_of_run_id")
    op.drop_column("evaluation_runs", "trigger_kind")
    op.create_check_constraint(
        "evaluation_runs_status_check",
        "evaluation_runs",
        "status IN ('running', 'succeeded', 'failed', 'skipped')",
    )
    op.create_unique_constraint(
        "evaluation_runs_portfolio_session_key",
        "evaluation_runs",
        ["portfolio_id", "scheduled_for"],
    )
    op.drop_index("idx_evaluator_instances_heartbeat", table_name="evaluator_instances")
    op.drop_table("evaluator_instances")
    op.drop_table("portfolio_evaluator_configs")
    op.drop_table("evaluator_settings")
