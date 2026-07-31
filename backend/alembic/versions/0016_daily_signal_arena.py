"""Replace rebuilt allocation history with immutable daily signal snapshots.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-30
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

REBUILT_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next independent signal
allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation policy,
and effective date as authoritative. The response intentionally excludes current holdings, signal
history, prior notes, performance, turnover, and transaction costs.

Act as a US equity portfolio manager aiming to outperform SPY. Search across the full eligible US
market rather than defaulting to index constituents, household names, or recent winners. Do not mirror
SPY. Select a stock or ETF only when it has a distinct, falsifiable, security-specific investment
thesis supported by current evidence.

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

REBUILT_PROMPTS = {
    "barebones-weekly": (
        "unrestricted-selection",
        "Unrestricted Selection",
        """\
Select the strongest evidence-backed opportunities for expected total return relative to SPY without
restricting the source of the edge.

For every candidate, identify the new, changing, or underprocessed fact; the market's apparent current
expectation; why that expectation is wrong; the causal path from the fact to earnings, cash flow,
valuation, or capital returns; why the gap remains unpriced; and the strongest contrary evidence and
downside. Rank candidates comparatively using quantitative evidence wherever possible.

Reject generic claims that a company is great, cheap, exposed to AI, oversold, or showing momentum
unless a specific and falsifiable mispricing explains why it should outperform. Avoid hidden
concentration in the same economic dependency across different securities.""",
    ),
    "weekly-economic-read-through": (
        "economic-read-through",
        "Economic Read-Through",
        """\
Select opportunities where fresh external economic, industry, supply-chain, demand, pricing, or
competitive evidence has a direct and underprocessed implication for a specific security.

State the full causal chain from the external evidence to the company's exposure, financial impact,
and expected repricing. Verify the exposure's size, contract structure, hedges, and reporting lags,
and explain why the market has not yet incorporated the implication.

Reject broad themes, stale correlations, indirect or immaterial exposures, and signals neutralized by
hedging, contract terms, mix, or an offsetting business effect.""",
    ),
    "weekly-fresh-information-repricing": (
        "fresh-information-repricing",
        "Fresh Information Repricing",
        """\
Select opportunities driven by newly public, material company-specific information that the market
has not fully processed.

Identify the publication and timestamp, the assumption or economic outcome it changes, why the
information remains underprocessed, and which audience or recognition process should close the gap.
Test the strongest alternative interpretation and distinguish new information from a restatement of
what investors already knew.

Reject stale, immaterial, speculative, fully priced, or causally remote information.""",
    ),
    "weekly-policy-and-geopolitical-anticipation": (
        "policy-and-geopolitical-anticipation",
        "Policy and Geopolitical Anticipation",
        """\
Select opportunities where observable policy or geopolitical developments create a differentiated,
evidence-backed expectation for a specific security.

Start with the concrete event. Analyze the relevant actors, incentives, constraints, precursors,
credible alternative outcomes, and the market's apparent expectation. Separate the probability of
the event from the security's likely reaction, then map the transmission mechanism and show why the
financial exposure is direct, material, and asymmetric.

Reject rumors, rhetoric without implementation evidence, generic thematic baskets, and broad outcome
guesses without a security-specific valuation gap.""",
    ),
    "weekly-post-earnings-underreaction": (
        "post-earnings-underreaction",
        "Post-Earnings Underreaction",
        """\
Select opportunities where a recent earnings release contains durable information that the market
has not fully incorporated.

Review the complete earnings materials rather than the headline. Identify the change in underlying
economics, its quality and durability, what the market emphasized, the facts it underprocessed, and
the recognition mechanism that can close the gap. Distinguish recurring operating evidence from
one-offs, accounting effects, and temporary timing.

Reject simple earnings-beat buying, headline or price momentum, low-quality surprises, and results
whose implications are already reflected in expectations and valuation.""",
    ),
    "weekly-pre-earnings-variant": (
        "pre-earnings-variant",
        "Pre-Earnings Variant",
        """\
Select opportunities with a confirmed upcoming earnings report where a differentiated, testable
expectation creates favorable asymmetry.

Reconstruct the true market bar from consensus estimates, guidance, segment assumptions, valuation,
and current channel or operating evidence. State the differentiated assumption, the metric most
likely to reveal it, how investors are likely to interpret the result, and the downside if the view
is wrong.

Reject routine previews, unknowable binary bets, crowded variants, and setups whose plausible upside
does not compensate for the downside.""",
    ),
    "weekly-scheduled-catalysts": (
        "scheduled-catalysts",
        "Scheduled Catalysts",
        """\
Select opportunities with a confirmed, dated, non-earnings catalyst that can materially change a
security's expected economics or valuation.

Identify the event and date, decision makers, prerequisites, plausible outcomes, priced expectation,
security-specific consequence, and downside. Prefer primary documentation and verify that the event
is actually scheduled and that its impact is not already reflected in price.

Reject rumors, undated possibilities, immaterial events, catalysts without an expectation gap, and
binary setups with unattractive asymmetry.""",
    ),
    "weekly-temporary-price-dislocation": (
        "temporary-price-dislocation",
        "Temporary Price Dislocation",
        """\
Select opportunities where forced, mechanical, or economically indifferent trading—or an excessive
reaction to a temporary event—has moved price away from a defensible value anchor.

Identify the source and trigger of the selling, its likely scale and duration, the value anchor, the
natural buyer or correction mechanism, and clear invalidation evidence. Demonstrate that the
underlying impairment is temporary rather than structural.

Reject squeeze theses, short-interest stories, generic oversold claims, falling knives, unresolved
accounting or financing risk, permanently impaired liquidity, and price declines justified by a
lasting deterioration in business value.""",
    ),
}


def upgrade() -> None:
    connection = op.get_bind()

    # Rebuilt v1 allocations are not comparable with the daily signal design.
    # Preserve every evaluation-run audit row while severing its deleted result.
    connection.execute(
        sa.text(
            """
            UPDATE evaluation_runs er
            SET allocation_id = NULL
            FROM allocations a, portfolios p
            WHERE er.allocation_id = a.id
              AND a.portfolio_id = p.id
              AND p.prompt_mode = 'rebuilt'
            """
        )
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM allocations a
            USING portfolios p
            WHERE a.portfolio_id = p.id
              AND p.prompt_mode = 'rebuilt'
            """
        )
    )

    # Benchmarks are synthetic in v2 and therefore have no mutable database
    # identity, evaluator configuration, or allocation history.
    connection.execute(sa.text("DELETE FROM portfolios WHERE is_benchmark"))
    op.drop_constraint(
        "portfolios_prompt_mode_assignment_check",
        "portfolios",
        type_="check",
    )
    op.drop_constraint(
        "portfolios_identity_assignment_check",
        "portfolios",
        type_="check",
    )
    op.drop_constraint("portfolios_prompt_mode_check", "portfolios", type_="check")
    op.alter_column("portfolios", "agent_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("portfolios", "prompt_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("portfolios", "prompt_mode", existing_type=sa.Text(), nullable=False)
    op.create_check_constraint(
        "portfolios_prompt_mode_check",
        "portfolios",
        "prompt_mode IN ('managed', 'rebuilt')",
    )
    op.add_column(
        "portfolios",
        sa.Column("founding_v2", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    connection.execute(sa.text("UPDATE portfolios SET founding_v2 = true WHERE prompt_mode = 'rebuilt'"))
    op.drop_column("portfolios", "is_benchmark")

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "provenance IN ('integrated', 'browser_admin', 'mcp')",
            name="signals_provenance_check",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "effective_date",
            name="signals_portfolio_id_effective_date_key",
        ),
    )
    op.create_index("idx_signals_portfolio_id", "signals", ["portfolio_id"])
    op.create_index(
        "idx_signals_effective_date_id",
        "signals",
        ["effective_date", "id"],
    )
    op.create_table(
        "signal_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("weight_pct", sa.Numeric(9, 4), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "weight_pct >= 0",
            name="signal_positions_weight_pct_check",
        ),
        sa.UniqueConstraint(
            "signal_id",
            "symbol",
            name="signal_positions_signal_id_symbol_key",
        ),
    )
    op.create_index(
        "idx_signal_positions_signal_id",
        "signal_positions",
        ["signal_id"],
    )

    op.add_column("evaluation_runs", sa.Column("signal_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "evaluation_runs_signal_id_fkey",
        "evaluation_runs",
        "signals",
        ["signal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "evaluation_runs_result_exclusive_check",
        "evaluation_runs",
        "NOT (allocation_id IS NOT NULL AND signal_id IS NOT NULL)",
    )

    # Integrated rebuilt portfolios run every US trading weekday. Managed
    # cadence remains operator-configurable.
    connection.execute(
        sa.text(
            """
            UPDATE portfolio_evaluator_configs pec
            SET weekdays = '[0, 1, 2, 3, 4]'::jsonb
            FROM portfolios p
            WHERE pec.portfolio_id = p.id
              AND p.prompt_mode = 'rebuilt'
            """
        )
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO settings (key, value)
            VALUES ('rebuilt_wrapper_prompt', :value)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
        ),
        {"value": REBUILT_WRAPPER},
    )
    for old_slug, (new_slug, name, text) in REBUILT_PROMPTS.items():
        connection.execute(
            sa.text(
                """
                UPDATE prompts
                SET slug = :new_slug, name = :name, text = :text
                WHERE slug = :old_slug
                """
            ),
            {
                "old_slug": old_slug,
                "new_slug": new_slug,
                "name": name,
                "text": text,
            },
        )


def downgrade() -> None:
    op.drop_constraint(
        "evaluation_runs_result_exclusive_check",
        "evaluation_runs",
        type_="check",
    )
    op.drop_constraint(
        "evaluation_runs_signal_id_fkey",
        "evaluation_runs",
        type_="foreignkey",
    )
    op.drop_column("evaluation_runs", "signal_id")
    op.drop_index("idx_signal_positions_signal_id", table_name="signal_positions")
    op.drop_table("signal_positions")
    op.drop_index("idx_signals_effective_date_id", table_name="signals")
    op.drop_index("idx_signals_portfolio_id", table_name="signals")
    op.drop_table("signals")

    op.add_column(
        "portfolios",
        sa.Column(
            "is_benchmark",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.drop_column("portfolios", "founding_v2")
    op.drop_constraint("portfolios_prompt_mode_check", "portfolios", type_="check")
    op.alter_column("portfolios", "prompt_mode", existing_type=sa.Text(), nullable=True)
    op.alter_column("portfolios", "prompt_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("portfolios", "agent_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "portfolios_prompt_mode_check",
        "portfolios",
        "prompt_mode IS NULL OR prompt_mode IN ('managed', 'rebuilt')",
    )
    op.create_check_constraint(
        "portfolios_identity_assignment_check",
        "portfolios",
        "(is_benchmark AND agent_id IS NULL AND prompt_id IS NULL) OR "
        "(NOT is_benchmark AND agent_id IS NOT NULL AND prompt_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "portfolios_prompt_mode_assignment_check",
        "portfolios",
        "(is_benchmark AND prompt_mode IS NULL) OR (NOT is_benchmark AND prompt_mode IS NOT NULL)",
    )
