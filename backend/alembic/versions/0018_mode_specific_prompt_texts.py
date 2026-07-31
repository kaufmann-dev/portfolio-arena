"""Split prompt strategy text by supported portfolio mode.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column("prompt_versions", sa.Column("mode", sa.Text(), nullable=True))
    op.add_column("prompt_versions", sa.Column("managed_text", sa.Text(), nullable=True))
    op.add_column("prompt_versions", sa.Column("rebuilt_text", sa.Text(), nullable=True))

    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions AS version
            SET
                mode = CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'managed'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'rebuilt'
                    ) THEN 'managed'
                    WHEN EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'rebuilt'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'managed'
                    ) THEN 'rebuilt'
                    ELSE 'both'
                END,
                managed_text = CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'rebuilt'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'managed'
                    ) THEN NULL
                    ELSE version.text
                END,
                rebuilt_text = CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'managed'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM portfolios
                        WHERE prompt_id = version.prompt_id
                          AND prompt_mode = 'rebuilt'
                    ) THEN NULL
                    ELSE version.text
                END
            """
        )
    )

    op.alter_column(
        "prompt_versions",
        "mode",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_check_constraint(
        "prompt_versions_mode_check",
        "prompt_versions",
        "mode IN ('managed', 'rebuilt', 'both')",
    )
    op.create_check_constraint(
        "prompt_versions_mode_texts_check",
        "prompt_versions",
        "(mode = 'managed' AND managed_text IS NOT NULL "
        "AND btrim(managed_text) <> '' AND rebuilt_text IS NULL) OR "
        "(mode = 'rebuilt' AND managed_text IS NULL "
        "AND rebuilt_text IS NOT NULL AND btrim(rebuilt_text) <> '') OR "
        "(mode = 'both' AND managed_text IS NOT NULL "
        "AND btrim(managed_text) <> '' AND rebuilt_text IS NOT NULL "
        "AND btrim(rebuilt_text) <> '')",
    )
    op.drop_column("prompt_versions", "text")


def downgrade() -> None:
    connection = op.get_bind()

    op.add_column("prompt_versions", sa.Column("text", sa.Text(), nullable=True))
    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET text = COALESCE(managed_text, rebuilt_text)
            """
        )
    )
    op.alter_column(
        "prompt_versions",
        "text",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint(
        "prompt_versions_mode_texts_check",
        "prompt_versions",
        type_="check",
    )
    op.drop_constraint(
        "prompt_versions_mode_check",
        "prompt_versions",
        type_="check",
    )
    op.drop_column("prompt_versions", "rebuilt_text")
    op.drop_column("prompt_versions", "managed_text")
    op.drop_column("prompt_versions", "mode")
