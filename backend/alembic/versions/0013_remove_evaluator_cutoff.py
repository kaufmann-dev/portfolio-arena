"""Allow scheduled evaluations to finish after market close.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("evaluator_settings_window_check", "evaluator_settings", type_="check")
    op.drop_constraint("evaluator_settings_cutoff_check", "evaluator_settings", type_="check")
    op.drop_constraint("evaluator_settings_start_check", "evaluator_settings", type_="check")
    op.drop_column("evaluator_settings", "cutoff_before_close_minutes")
    op.alter_column(
        "evaluator_settings",
        "start_before_close_minutes",
        new_column_name="queue_before_close_minutes",
    )
    op.create_check_constraint(
        "evaluator_settings_queue_check",
        "evaluator_settings",
        "queue_before_close_minutes BETWEEN 15 AND 240",
    )
    op.drop_column("evaluation_runs", "deadline_at")


def downgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("evaluator_settings_queue_check", "evaluator_settings", type_="check")
    op.alter_column(
        "evaluator_settings",
        "queue_before_close_minutes",
        new_column_name="start_before_close_minutes",
    )
    op.add_column(
        "evaluator_settings",
        sa.Column(
            "cutoff_before_close_minutes",
            sa.Integer(),
            server_default="10",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "evaluator_settings_start_check",
        "evaluator_settings",
        "start_before_close_minutes BETWEEN 15 AND 240",
    )
    op.create_check_constraint(
        "evaluator_settings_cutoff_check",
        "evaluator_settings",
        "cutoff_before_close_minutes BETWEEN 0 AND 60",
    )
    op.create_check_constraint(
        "evaluator_settings_window_check",
        "evaluator_settings",
        "start_before_close_minutes > cutoff_before_close_minutes",
    )
