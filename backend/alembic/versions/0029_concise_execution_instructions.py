"""Separate direction, sizing, and managed instructions in saved prompts.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

LONG_BEFORE = """\
- This is an all-long portfolio. Every submitted position is a long position.
- Submit security weights according to the allocation policy; the server puts any remainder in long SPY.
- Do not submit cash, shorts, leverage, or placeholder reference tickers."""
LONG_AFTER = (
    "This is an all-long portfolio. Submit positive weights for securities expected to outperform SPY."
)
SHORT_BEFORE = "\n".join(
    [
        "- This is an all-short portfolio. Select securities whose prices are expected to underperform SPY "
        "so the short book can outperform the Short SPY reference.",
        "- Submit positive security weights according to the allocation policy; the server interprets "
        "them as short exposure and puts any remainder in the synthetic Short SPY reference.",
        "- Do not submit cash, long positions, gross exposure above 100%, or placeholder reference tickers.",
    ]
)
SHORT_AFTER = """\
This is an all-short portfolio. Select securities whose prices are expected to underperform SPY
so the short book can outperform the Short SPY reference. Submit positive weights; the server
interprets them as short exposure."""
MANAGED_DECISION = "This decision replaces previous holdings; abstaining moves them into the reference."


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT key, value FROM settings WHERE key IN "
            "('long_direction_instructions', 'short_direction_instructions', "
            "'managed_wrapper_prompt', 'rebuilt_wrapper_prompt')"
        )
    ).all()
    for key, value in rows:
        updated = value.replace(LONG_BEFORE, LONG_AFTER).replace(SHORT_BEFORE, SHORT_AFTER)
        updated = updated.replace("; the server supplies any automatic reference allocation.", ".")
        updated = updated.replace("; the server supplies\nany automatic reference allocation.", ".")
        if key == "managed_wrapper_prompt" and MANAGED_DECISION not in updated:
            updated = updated.replace(
                "{{allocation_policy}}", "{{allocation_policy}}\n\n" + MANAGED_DECISION, 1
            )
        if updated != value:
            connection.execute(
                sa.text("UPDATE settings SET value = :value WHERE key = :key"), {"key": key, "value": updated}
            )


def downgrade() -> None:
    # Preserve editable prompt text rather than restoring redundant wording.
    pass
