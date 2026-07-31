"""Version prompt content and add portfolio direction.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column(
        "portfolios",
        sa.Column("direction", sa.Text(), nullable=False, server_default="long"),
    )
    op.create_check_constraint(
        "portfolios_direction_check",
        "portfolios",
        "direction IN ('long', 'short')",
    )
    op.alter_column(
        "portfolios",
        "direction",
        existing_type=sa.Text(),
        server_default=None,
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "prompt_id",
            sa.Integer(),
            sa.ForeignKey("prompts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_position_weight_pct", sa.Numeric(9, 4), nullable=False),
        sa.Column("max_position_weight_pct", sa.Numeric(9, 4), nullable=False),
        sa.Column(
            "restored_from_version_id",
            sa.Integer(),
            sa.ForeignKey("prompt_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="prompt_versions_version_check"),
        sa.CheckConstraint(
            "min_position_weight_pct > 0 AND max_position_weight_pct <= 100 "
            "AND min_position_weight_pct <= max_position_weight_pct",
            name="prompt_versions_position_weights_check",
        ),
        sa.UniqueConstraint(
            "prompt_id",
            "version",
            name="prompt_versions_prompt_id_version_key",
        ),
    )
    op.create_index(
        "idx_prompt_versions_prompt_id",
        "prompt_versions",
        ["prompt_id"],
    )

    op.add_column(
        "prompts",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column(
        "prompts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "prompts",
        sa.Column("current_version_id", sa.Integer(), nullable=True),
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO prompt_versions (
                prompt_id,
                version,
                name,
                text,
                notes,
                min_position_weight_pct,
                max_position_weight_pct,
                created_at
            )
            SELECT
                id,
                1,
                name,
                text,
                notes,
                min_position_weight_pct,
                max_position_weight_pct,
                updated_at
            FROM prompts
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE prompts AS p
            SET current_version_id = pv.id
            FROM prompt_versions AS pv
            WHERE pv.prompt_id = p.id
              AND pv.version = 1
            """
        )
    )

    op.create_foreign_key(
        "prompts_current_version_id_fkey",
        "prompts",
        "prompt_versions",
        ["current_version_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "prompts_status_check",
        "prompts",
        "status IN ('active', 'archived')",
    )
    op.create_check_constraint(
        "prompts_archive_state_check",
        "prompts",
        "(status = 'active' AND archived_at IS NULL) OR (status = 'archived' AND archived_at IS NOT NULL)",
    )

    connection.execute(
        sa.text(
            """
            UPDATE settings
            SET value = replace(
                value,
                'aiming to outperform SPY',
                'aiming to outperform the portfolio''s direction-matched SPY reference'
            )
            WHERE key IN ('managed_wrapper_prompt', 'rebuilt_wrapper_prompt')
            """
        )
    )

    op.drop_constraint("prompts_position_weights_check", "prompts", type_="check")
    op.drop_column("prompts", "max_position_weight_pct")
    op.drop_column("prompts", "min_position_weight_pct")
    op.drop_column("prompts", "notes")
    op.drop_column("prompts", "text")
    op.drop_column("prompts", "name")


def downgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            UPDATE settings
            SET value = replace(
                value,
                'aiming to outperform the portfolio''s direction-matched SPY reference',
                'aiming to outperform SPY'
            )
            WHERE key IN ('managed_wrapper_prompt', 'rebuilt_wrapper_prompt')
            """
        )
    )

    op.add_column("prompts", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("prompts", sa.Column("text", sa.Text(), nullable=True))
    op.add_column(
        "prompts",
        sa.Column("notes", sa.Text(), nullable=True, server_default=""),
    )
    op.add_column(
        "prompts",
        sa.Column("min_position_weight_pct", sa.Numeric(9, 4), nullable=True),
    )
    op.add_column(
        "prompts",
        sa.Column("max_position_weight_pct", sa.Numeric(9, 4), nullable=True),
    )
    connection.execute(
        sa.text(
            """
            UPDATE prompts AS p
            SET
                name = pv.name,
                text = pv.text,
                notes = pv.notes,
                min_position_weight_pct = pv.min_position_weight_pct,
                max_position_weight_pct = pv.max_position_weight_pct
            FROM prompt_versions AS pv
            WHERE pv.id = p.current_version_id
            """
        )
    )
    op.alter_column("prompts", "name", existing_type=sa.Text(), nullable=False)
    op.alter_column("prompts", "text", existing_type=sa.Text(), nullable=False)
    op.alter_column("prompts", "notes", existing_type=sa.Text(), nullable=False)
    op.alter_column(
        "prompts",
        "min_position_weight_pct",
        existing_type=sa.Numeric(9, 4),
        nullable=False,
    )
    op.alter_column(
        "prompts",
        "max_position_weight_pct",
        existing_type=sa.Numeric(9, 4),
        nullable=False,
    )
    op.create_check_constraint(
        "prompts_position_weights_check",
        "prompts",
        "min_position_weight_pct > 0 AND max_position_weight_pct <= 100 "
        "AND min_position_weight_pct <= max_position_weight_pct",
    )

    op.drop_constraint("prompts_archive_state_check", "prompts", type_="check")
    op.drop_constraint("prompts_status_check", "prompts", type_="check")
    op.drop_constraint(
        "prompts_current_version_id_fkey",
        "prompts",
        type_="foreignkey",
    )
    op.drop_column("prompts", "current_version_id")
    op.drop_column("prompts", "archived_at")
    op.drop_column("prompts", "status")
    op.drop_index("idx_prompt_versions_prompt_id", table_name="prompt_versions")
    op.drop_table("prompt_versions")

    op.drop_constraint("portfolios_direction_check", "portfolios", type_="check")
    op.drop_column("portfolios", "direction")
