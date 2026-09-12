"""Allow reference-backed decisions in persisted execution instructions.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    replacements = [
        (
            "- Invest exactly 100% of NAV across USD-denominated equities and ETFs.",
            "- Submit security weights according to the allocation policy; "
            "the server puts any remainder in long SPY.",
        ),
        (
            "- Submit positive weights totaling exactly 100%; the server interprets every position "
            "as gross short exposure.",
            "- Submit positive security weights according to the allocation policy; "
            "the server interprets them as short exposure and puts any remainder "
            "in the synthetic Short SPY reference.",
        ),
        (
            "- Do not use cash, shorts, or leverage.",
            "- Do not submit cash, shorts, leverage, or placeholder reference tickers.",
        ),
        (
            "- Do not use cash, long positions, or gross exposure above 100%.",
            "- Do not submit cash, long positions, gross exposure above 100%, "
            "or placeholder reference tickers.",
        ),
        (
            "Do not mirror SPY.",
            "Do not select SPY as a substitute for research; "
            "the server supplies any automatic reference allocation.",
        ),
    ]
    rows = connection.execute(
        sa.text(
            "SELECT key, value FROM settings WHERE key IN "
            "('long_direction_instructions', 'short_direction_instructions', "
            "'managed_wrapper_prompt', 'rebuilt_wrapper_prompt')"
        )
    ).all()
    for key, value in rows:
        updated = value
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != value:
            connection.execute(
                sa.text("UPDATE settings SET value = :value WHERE key = :key"), {"key": key, "value": updated}
            )


def downgrade() -> None:
    # Instruction edits cannot undo decisions already submitted under the new policy.
    pass
