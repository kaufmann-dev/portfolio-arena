"""Raise the evaluator attempt timeout limit.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-27
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("evaluator_settings_timeout_check", "evaluator_settings", type_="check")
    op.create_check_constraint(
        "evaluator_settings_timeout_check",
        "evaluator_settings",
        "attempt_timeout_seconds BETWEEN 60 AND 7200",
    )
    op.drop_constraint("evaluation_runs_timeout_check", "evaluation_runs", type_="check")
    op.create_check_constraint(
        "evaluation_runs_timeout_check",
        "evaluation_runs",
        "timeout_seconds BETWEEN 60 AND 7200",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE evaluator_settings SET attempt_timeout_seconds = 1500 WHERE attempt_timeout_seconds > 1500"
    )
    op.execute("UPDATE evaluation_runs SET timeout_seconds = 1500 WHERE timeout_seconds > 1500")
    op.drop_constraint("evaluator_settings_timeout_check", "evaluator_settings", type_="check")
    op.create_check_constraint(
        "evaluator_settings_timeout_check",
        "evaluator_settings",
        "attempt_timeout_seconds BETWEEN 60 AND 1500",
    )
    op.drop_constraint("evaluation_runs_timeout_check", "evaluation_runs", type_="check")
    op.create_check_constraint(
        "evaluation_runs_timeout_check",
        "evaluation_runs",
        "timeout_seconds BETWEEN 60 AND 1500",
    )
