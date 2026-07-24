"""Share strategy prompts across managed and rebuilt execution modes.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


MANAGED_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, current holdings, notes, allocation history, performance, and effective date as authoritative.

If the returned allocation history is empty, construct the portfolio's initial allocation. Otherwise,
manage and rebalance the existing portfolio; do not rebuild it from scratch. Treat each evaluation as
an opportunity to update the evidence, not as an instruction to trade. Reassess every holding and
credible candidate using current evidence and current prices. Every holding must re-earn its place,
but prefer retaining the existing allocation when its theses, forward risk-adjusted returns, and
portfolio risks remain substantially unchanged. Change a holding or target weight only when durable,
strategy-relevant evidence indicates that doing so should meaningfully improve the portfolio after
transaction costs. Do not trade solely because of ordinary price noise, repeated information that
does not alter the evidence, small or unstable ranking differences, or immaterial weight drift within
the allocation policy.

Strategy:
{{strategy_text}}

Allocation policy:
{{allocation_policy}}

Research all decision-relevant current information with Massive and live web search.

{{submission_instructions}}"""

REBUILT_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, and effective date as authoritative. The response intentionally excludes current holdings,
allocation history, prior notes, portfolio performance, turnover, and transaction costs.

At every evaluation, rebuild the complete target portfolio independently from scratch across the full
eligible universe using current evidence and the strategy. Give no preference to a previously held
security and no penalty to replacing it. A security may be selected again only if it independently
qualifies as one of the best current opportunities. Produce a complete target allocation at every
evaluation.

Strategy:
{{strategy_text}}

Allocation policy:
{{allocation_policy}}

Research all decision-relevant current information with Massive and live web search.

{{submission_instructions}}"""

PROMPT_PAIRS = {
    "barebones-weekly-rebuilt": "barebones",
    "capital-cycle-weekly-rebuilt": "moat-durability",
    "catalyst-weekly-rebuilt": "momentum",
    "contrarian-misperception-weekly-rebuilt": "mispricing",
    "narrative-diffusion-weekly-rebuilt": "narrative-diffusion",
    "quality-compounder-weekly-rebuilt": "quality-compounder",
    "regime-interpreter-weekly-rebuilt": "standard",
    "resilience-weekly-rebuilt": "resilience",
    "secular-change-weekly-rebuilt": "consensus",
    "structural-inefficiency-weekly-rebuilt": "structural-inefficiency",
}

CANONICAL_PROMPTS = {
    "barebones": ("barebones", "Barebones"),
    "moat-durability": ("capital-cycle", "Capital Cycle"),
    "momentum": ("catalyst", "Catalyst"),
    "mispricing": ("contrarian-misperception", "Contrarian Misperception"),
    "narrative-diffusion": ("narrative-diffusion", "Narrative Diffusion"),
    "quality-compounder": ("quality-compounder", "Quality Compounder"),
    "standard": ("regime-interpreter", "Regime Interpreter"),
    "resilience": ("resilience", "Resilience"),
    "consensus": ("secular-change", "Secular Change"),
    "structural-inefficiency": ("structural-inefficiency", "Structural Inefficiency"),
}


def upgrade() -> None:
    op.add_column("portfolios", sa.Column("prompt_mode", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE portfolios p
        SET prompt_mode = CASE
            WHEN p.is_benchmark THEN NULL
            WHEN EXISTS (
                SELECT 1 FROM prompts pr
                WHERE pr.id = p.prompt_id AND pr.slug LIKE '%-weekly-rebuilt'
            ) THEN 'rebuilt'
            ELSE 'managed'
        END
        """
    )
    op.create_check_constraint(
        "portfolios_prompt_mode_check",
        "portfolios",
        "prompt_mode IS NULL OR prompt_mode IN ('managed', 'rebuilt')",
    )
    op.create_check_constraint(
        "portfolios_prompt_mode_assignment_check",
        "portfolios",
        "(is_benchmark AND prompt_mode IS NULL) OR (NOT is_benchmark AND prompt_mode IS NOT NULL)",
    )

    connection = op.get_bind()
    for weekly_slug, retained_slug in PROMPT_PAIRS.items():
        connection.execute(
            sa.text(
                """
                UPDATE portfolios
                SET prompt_id = (SELECT id FROM prompts WHERE slug = :retained_slug)
                WHERE prompt_id = (SELECT id FROM prompts WHERE slug = :weekly_slug)
                """
            ),
            {"retained_slug": retained_slug, "weekly_slug": weekly_slug},
        )
        connection.execute(
            sa.text("DELETE FROM prompts WHERE slug = :weekly_slug"),
            {"weekly_slug": weekly_slug},
        )

    for old_slug, (canonical_slug, canonical_name) in CANONICAL_PROMPTS.items():
        connection.execute(
            sa.text(
                """
                UPDATE prompts
                SET slug = :canonical_slug,
                    name = :canonical_name,
                    text = split_part(text, E'\\n\\nPortfolio lifecycle:\\n', 1)
                WHERE slug = :old_slug
                """
            ),
            {
                "canonical_slug": canonical_slug,
                "canonical_name": canonical_name,
                "old_slug": old_slug,
            },
        )

    for key, value in (
        ("managed_wrapper_prompt", MANAGED_WRAPPER),
        ("rebuilt_wrapper_prompt", REBUILT_WRAPPER),
    ):
        connection.execute(
            sa.text(
                """
                INSERT INTO settings (key, value) VALUES (:key, :value)
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {"key": key, "value": value},
        )


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key IN ('managed_wrapper_prompt', 'rebuilt_wrapper_prompt')")
    op.drop_constraint(
        "portfolios_prompt_mode_assignment_check",
        "portfolios",
        type_="check",
    )
    op.drop_constraint("portfolios_prompt_mode_check", "portfolios", type_="check")
    op.drop_column("portfolios", "prompt_mode")
