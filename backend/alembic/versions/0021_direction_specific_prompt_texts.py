"""Store distinct strategy text for every supported mode and direction.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


PROMPT_TEXTS_CHECK = """
((mode IN ('managed', 'both') AND direction IN ('long', 'both')
    AND managed_long_text IS NOT NULL AND btrim(managed_long_text) <> '') OR
 (NOT (mode IN ('managed', 'both') AND direction IN ('long', 'both'))
    AND managed_long_text IS NULL)) AND
((mode IN ('managed', 'both') AND direction IN ('short', 'both')
    AND managed_short_text IS NOT NULL AND btrim(managed_short_text) <> '') OR
 (NOT (mode IN ('managed', 'both') AND direction IN ('short', 'both'))
    AND managed_short_text IS NULL)) AND
((mode IN ('rebuilt', 'both') AND direction IN ('long', 'both')
    AND rebuilt_long_text IS NOT NULL AND btrim(rebuilt_long_text) <> '') OR
 (NOT (mode IN ('rebuilt', 'both') AND direction IN ('long', 'both'))
    AND rebuilt_long_text IS NULL)) AND
((mode IN ('rebuilt', 'both') AND direction IN ('short', 'both')
    AND rebuilt_short_text IS NOT NULL AND btrim(rebuilt_short_text) <> '') OR
 (NOT (mode IN ('rebuilt', 'both') AND direction IN ('short', 'both'))
    AND rebuilt_short_text IS NULL))
"""

LEGACY_PROMPT_TEXTS_CHECK = """
(mode = 'managed' AND managed_text IS NOT NULL AND btrim(managed_text) <> ''
    AND rebuilt_text IS NULL) OR
(mode = 'rebuilt' AND managed_text IS NULL AND rebuilt_text IS NOT NULL
    AND btrim(rebuilt_text) <> '') OR
(mode = 'both' AND managed_text IS NOT NULL AND btrim(managed_text) <> ''
    AND rebuilt_text IS NOT NULL AND btrim(rebuilt_text) <> '')
"""


def upgrade() -> None:
    connection = op.get_bind()

    op.drop_constraint("prompt_versions_mode_texts_check", "prompt_versions", type_="check")
    op.alter_column("prompt_versions", "managed_text", new_column_name="managed_long_text")
    op.alter_column("prompt_versions", "rebuilt_text", new_column_name="rebuilt_long_text")
    op.add_column("prompt_versions", sa.Column("managed_short_text", sa.Text(), nullable=True))
    op.add_column("prompt_versions", sa.Column("rebuilt_short_text", sa.Text(), nullable=True))

    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET managed_short_text = managed_long_text,
                rebuilt_short_text = rebuilt_long_text
            WHERE direction IN ('short', 'both')
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET managed_long_text = NULL,
                rebuilt_long_text = NULL
            WHERE direction = 'short'
            """
        )
    )
    op.create_check_constraint(
        "prompt_versions_mode_texts_check",
        "prompt_versions",
        PROMPT_TEXTS_CHECK,
    )


def downgrade() -> None:
    connection = op.get_bind()

    op.drop_constraint("prompt_versions_mode_texts_check", "prompt_versions", type_="check")
    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET managed_long_text = managed_short_text,
                rebuilt_long_text = rebuilt_short_text
            WHERE direction = 'short'
            """
        )
    )
    op.drop_column("prompt_versions", "rebuilt_short_text")
    op.drop_column("prompt_versions", "managed_short_text")
    op.alter_column("prompt_versions", "rebuilt_long_text", new_column_name="rebuilt_text")
    op.alter_column("prompt_versions", "managed_long_text", new_column_name="managed_text")
    op.create_check_constraint(
        "prompt_versions_mode_texts_check",
        "prompt_versions",
        LEGACY_PROMPT_TEXTS_CHECK,
    )
