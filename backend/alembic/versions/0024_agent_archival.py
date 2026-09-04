"""Add reversible lifecycle state for agents.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column(
        "agents",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "agents_status_check",
        "agents",
        "status IN ('active', 'archived')",
    )
    op.create_check_constraint(
        "agents_archive_state_check",
        "agents",
        "(status = 'active' AND archived_at IS NULL) OR (status = 'archived' AND archived_at IS NOT NULL)",
    )
    op.drop_index("agents_execution_profile_key", table_name="agents")
    op.create_index(
        "agents_execution_profile_key",
        "agents",
        [
            "model_id",
            sa.text("coalesce(harness, '')"),
            sa.text("coalesce(reasoning_effort, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("agents_execution_profile_key", table_name="agents")
    op.create_index(
        "agents_execution_profile_key",
        "agents",
        [
            "model_id",
            sa.text("coalesce(harness, '')"),
            sa.text("coalesce(reasoning_effort, '')"),
        ],
        unique=True,
    )
    op.drop_constraint("agents_archive_state_check", "agents", type_="check")
    op.drop_constraint("agents_status_check", "agents", type_="check")
    op.drop_column("agents", "archived_at")
    op.drop_column("agents", "status")
