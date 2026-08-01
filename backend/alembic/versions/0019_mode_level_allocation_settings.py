"""Move allocation sizing from prompts to global mode settings.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


OLD_REBUILT_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next independent signal
allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation policy,
and effective date as authoritative. The response intentionally excludes current holdings, signal
history, prior notes, performance, turnover, and transaction costs.

Act as a US equity portfolio manager aiming to outperform the portfolio's direction-matched SPY
reference. Search across the full eligible US market rather than defaulting to index constituents,
household names, or recent winners. Do not mirror SPY. Select a stock or ETF only when it has a
distinct, falsifiable, security-specific investment thesis supported by current evidence.

At every evaluation, construct the complete signal portfolio independently from scratch. Evaluate
every candidate without regard to previous signals. Select each security only if it independently
qualifies as one of the strongest current opportunities under the strategy, and produce a complete
target allocation.

Strategy:
{{strategy_text}}

Allocation policy:
{{allocation_policy}}

Research all decision-relevant current information with Massive and live web search.

{{submission_instructions}}"""

NEW_REBUILT_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next independent signal
allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, and effective date as authoritative. The response intentionally excludes current holdings,
signal history, prior notes, performance, turnover, and transaction costs.

Act as a US equity security selector aiming to outperform the portfolio's direction-matched SPY
reference. Search across the full eligible US market rather than defaulting to index constituents,
household names, or recent winners. Do not mirror SPY.

This is an independent security-selection signal. Breadth must be an outcome of the evidence, not a
diversification target. Select only securities that independently qualify under the strategy. Never
add a marginal security merely to increase the position count or make the allocation appear more
diversified.

Weight qualifying securities comparatively using expected excess return, conviction, evidence
quality, downside, and the strength and timing of the recognition mechanism. Stronger opportunities
should receive higher weights. A single security may receive 100% when it is the only opportunity
that genuinely qualifies, but concentration must reflect the evidence rather than convenience or
familiarity.

At every evaluation, construct the complete signal independently from scratch. Evaluate every
candidate without regard to previous signals, and do not infer, preserve, or reverse-engineer any
previous allocation.

Strategy:
{{strategy_text}}

Allocation policy:
{{allocation_policy}}

Research all decision-relevant current information with Massive and live web search.

{{submission_instructions}}"""

POLICY_DEFAULTS = {
    "managed_min_position_weight_pct": "10",
    "managed_max_position_weight_pct": "25",
    "rebuilt_min_position_weight_pct": "10",
    "rebuilt_max_position_weight_pct": "100",
}


def _replace_rebuilt_wrapper(old_value: str, new_value: str) -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE settings
            SET value = :new_value
            WHERE key = 'rebuilt_wrapper_prompt'
              AND regexp_replace(value, '\\s+', ' ', 'g')
                  = regexp_replace(:old_value, '\\s+', ' ', 'g')
            """
        ),
        {"old_value": old_value, "new_value": new_value},
    )


def upgrade() -> None:
    connection = op.get_bind()
    for key, value in POLICY_DEFAULTS.items():
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

    _replace_rebuilt_wrapper(OLD_REBUILT_WRAPPER, NEW_REBUILT_WRAPPER)
    op.drop_constraint(
        "prompt_versions_position_weights_check",
        "prompt_versions",
        type_="check",
    )
    op.drop_column("prompt_versions", "max_position_weight_pct")
    op.drop_column("prompt_versions", "min_position_weight_pct")


def downgrade() -> None:
    connection = op.get_bind()
    op.add_column(
        "prompt_versions",
        sa.Column("min_position_weight_pct", sa.Numeric(9, 4), nullable=True),
    )
    op.add_column(
        "prompt_versions",
        sa.Column("max_position_weight_pct", sa.Numeric(9, 4), nullable=True),
    )
    connection.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET
                min_position_weight_pct = CAST(
                    (SELECT value FROM settings WHERE key = CASE
                        WHEN prompt_versions.mode = 'rebuilt'
                            THEN 'rebuilt_min_position_weight_pct'
                        ELSE 'managed_min_position_weight_pct'
                    END) AS NUMERIC
                ),
                max_position_weight_pct = CAST(
                    (SELECT value FROM settings WHERE key = CASE
                        WHEN prompt_versions.mode = 'rebuilt'
                            THEN 'rebuilt_max_position_weight_pct'
                        ELSE 'managed_max_position_weight_pct'
                    END) AS NUMERIC
                )
            """
        )
    )
    op.alter_column(
        "prompt_versions",
        "min_position_weight_pct",
        existing_type=sa.Numeric(9, 4),
        nullable=False,
    )
    op.alter_column(
        "prompt_versions",
        "max_position_weight_pct",
        existing_type=sa.Numeric(9, 4),
        nullable=False,
    )
    op.create_check_constraint(
        "prompt_versions_position_weights_check",
        "prompt_versions",
        "min_position_weight_pct > 0 AND max_position_weight_pct <= 100 "
        "AND min_position_weight_pct <= max_position_weight_pct",
    )
    _replace_rebuilt_wrapper(NEW_REBUILT_WRAPPER, OLD_REBUILT_WRAPPER)
    connection.execute(
        sa.text(
            """
            DELETE FROM settings
            WHERE key IN (
                'managed_min_position_weight_pct',
                'managed_max_position_weight_pct',
                'rebuilt_min_position_weight_pct',
                'rebuilt_max_position_weight_pct'
            )
            """
        )
    )
