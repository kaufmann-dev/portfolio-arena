"""Add prompt direction support and editable direction instructions.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


LONG_DIRECTION_INSTRUCTIONS = """\
- This is an all-long portfolio. Every submitted position is a long position.
- Invest exactly 100% of NAV across USD-denominated equities and ETFs.
- Do not use cash, shorts, or leverage."""

SHORT_DIRECTION_INSTRUCTIONS = "\n".join(
    [
        (
            "- This is an all-short portfolio. Select securities whose prices are expected to "
            "underperform SPY so the short book can outperform the Short SPY reference."
        ),
        (
            "- Submit positive weights totaling exactly 100%; the server interprets every position "
            "as gross short exposure."
        ),
        "- Do not use cash, long positions, or gross exposure above 100%.",
    ]
)

DIRECTION_BLOCK = "Direction instructions:\n{{direction_instructions}}"
STRATEGY_ANCHOR = "Strategy:\n{{strategy_text}}"


def _add_direction_block(value: str) -> str:
    if "{{direction_instructions}}" in value:
        return value
    if STRATEGY_ANCHOR in value:
        return value.replace(STRATEGY_ANCHOR, f"{DIRECTION_BLOCK}\n\n{STRATEGY_ANCHOR}", 1)
    return f"{value.rstrip()}\n\n{DIRECTION_BLOCK}"


def _remove_direction_block(value: str) -> str:
    before_strategy = f"{DIRECTION_BLOCK}\n\n{STRATEGY_ANCHOR}"
    if before_strategy in value:
        return value.replace(before_strategy, STRATEGY_ANCHOR, 1)
    trailing = f"\n\n{DIRECTION_BLOCK}"
    if value.endswith(trailing):
        return value[: -len(trailing)]
    return value.replace("{{direction_instructions}}", "")


def _rewrite_wrappers(transform) -> None:
    connection = op.get_bind()
    for key in ("managed_wrapper_prompt", "rebuilt_wrapper_prompt"):
        value = connection.execute(
            sa.text("SELECT value FROM settings WHERE key = :key"),
            {"key": key},
        ).scalar_one_or_none()
        if value is None:
            continue
        connection.execute(
            sa.text("UPDATE settings SET value = :value WHERE key = :key"),
            {"key": key, "value": transform(value)},
        )


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column("prompt_versions", sa.Column("direction", sa.Text(), nullable=True))
    connection.execute(sa.text("UPDATE prompt_versions SET direction = 'long'"))
    op.alter_column("prompt_versions", "direction", existing_type=sa.Text(), nullable=False)
    op.create_check_constraint(
        "prompt_versions_direction_check",
        "prompt_versions",
        "direction IN ('long', 'short', 'both')",
    )

    for key, value in (
        ("long_direction_instructions", LONG_DIRECTION_INSTRUCTIONS),
        ("short_direction_instructions", SHORT_DIRECTION_INSTRUCTIONS),
    ):
        connection.execute(
            sa.text(
                """
                INSERT INTO settings (key, value)
                VALUES (:key, :value)
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {"key": key, "value": value},
        )
    _rewrite_wrappers(_add_direction_block)


def downgrade() -> None:
    connection = op.get_bind()
    _rewrite_wrappers(_remove_direction_block)
    connection.execute(
        sa.text(
            """
            DELETE FROM settings
            WHERE key IN ('long_direction_instructions', 'short_direction_instructions')
            """
        )
    )
    op.drop_constraint("prompt_versions_direction_check", "prompt_versions", type_="check")
    op.drop_column("prompt_versions", "direction")
