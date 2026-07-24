"""Refine the default managed and rebuilt wrapper prompts.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


OLD_MANAGED_WRAPPER = """\
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

OLD_REBUILT_WRAPPER = """\
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

NEW_MANAGED_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, current holdings, notes, allocation history, performance, and effective date as authoritative.

Act as a US equity portfolio manager aiming to outperform SPY. Search across the full eligible US
market rather than defaulting to index constituents, household names, or recent winners. Do not mirror
SPY. Select a stock or ETF only when it has a distinct, falsifiable, security-specific investment
thesis supported by current evidence.

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

NEW_REBUILT_WRAPPER = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, and effective date as authoritative. The response intentionally excludes current holdings,
allocation history, prior notes, portfolio performance, turnover, and transaction costs.

Act as a US equity portfolio manager aiming to outperform SPY. Search across the full eligible US
market rather than defaulting to index constituents, household names, or recent winners. Do not mirror
SPY. Select a stock or ETF only when it has a distinct, falsifiable, security-specific investment
thesis supported by current evidence.

At every evaluation, rebuild the complete target portfolio independently from scratch across the full
eligible universe using current evidence and the strategy. Evaluate every candidate without regard to
the previous portfolio. Select each security only if it independently qualifies as one of the best
current opportunities. Produce a complete target allocation at every evaluation.

Strategy:
{{strategy_text}}

Allocation policy:
{{allocation_policy}}

Research all decision-relevant current information with Massive and live web search.

{{submission_instructions}}"""


def _replace_default(key: str, old_value: str, new_value: str) -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE settings
            SET value = :new_value
            WHERE key = :key AND value = :old_value
            """
        ),
        {"key": key, "old_value": old_value, "new_value": new_value},
    )


def upgrade() -> None:
    _replace_default("managed_wrapper_prompt", OLD_MANAGED_WRAPPER, NEW_MANAGED_WRAPPER)
    _replace_default("rebuilt_wrapper_prompt", OLD_REBUILT_WRAPPER, NEW_REBUILT_WRAPPER)


def downgrade() -> None:
    _replace_default("managed_wrapper_prompt", NEW_MANAGED_WRAPPER, OLD_MANAGED_WRAPPER)
    _replace_default("rebuilt_wrapper_prompt", NEW_REBUILT_WRAPPER, OLD_REBUILT_WRAPPER)
