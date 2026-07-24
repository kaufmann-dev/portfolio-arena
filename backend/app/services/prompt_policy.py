"""Prompt allocation policy helpers and execution-wrapper rendering."""

import math
import re

from ..models import Portfolio, Prompt

PROMPT_MODES = {"managed", "rebuilt"}
WRAPPER_PLACEHOLDERS = {
    "portfolio_slug",
    "strategy_text",
    "allocation_policy",
    "submission_instructions",
}
_WRAPPER_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")

DEFAULT_MANAGED_WRAPPER_PROMPT = """\
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

DEFAULT_REBUILT_WRAPPER_PROMPT = """\
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

AUTOMATED_SUBMISSION_INSTRUCTIONS = """\
Do not call any write tool: the worker will validate and submit the final structured proposal
atomically.

The report should briefly explain the portfolio-level decision, key evidence, material risks, and
what would change the next evaluation. Position notes should be concise handoff context.

Return `status` as `proposal` with an empty `error` for a valid allocation. If `get_portfolio` fails
or no valid allocation can be produced, return `status` as `blocked`, no positions, and a concise
`error`; never invent a placeholder symbol."""

MANUAL_SUBMISSION_INSTRUCTIONS = """\
When the analysis is complete, call `create_allocation` exactly once with the portfolio id returned by
`get_portfolio`. Include a concise portfolio-level note and useful per-position notes so the next
evaluation can understand the decision."""


def allocation_policy_from_limits(minimum: float, maximum: float) -> dict:
    return {
        "min_position_weight_pct": minimum,
        "max_position_weight_pct": maximum,
        "derived_min_positions": math.ceil(100 / maximum),
        "derived_max_positions": math.floor(100 / minimum),
    }


def allocation_policy_out(prompt: Prompt) -> dict:
    return allocation_policy_from_limits(
        float(prompt.min_position_weight_pct),
        float(prompt.max_position_weight_pct),
    )


def validate_position_weights(prompt: Prompt, positions: list[dict]) -> None:
    """Raise ``ValueError`` when a position violates the prompt's active policy."""
    policy = allocation_policy_out(prompt)
    minimum = policy["min_position_weight_pct"]
    maximum = policy["max_position_weight_pct"]
    for position in positions:
        weight = float(position["weight_pct"])
        if weight < minimum or weight > maximum:
            raise ValueError(f"{position['symbol']} weight must be between {minimum:g}% and {maximum:g}%.")


def validate_wrapper_prompt(template: str) -> str:
    """Validate and return an editable execution-wrapper template."""
    if not template.strip():
        raise ValueError("Wrapper prompt cannot be blank.")
    matches = _WRAPPER_PLACEHOLDER_RE.findall(template)
    unmatched = _WRAPPER_PLACEHOLDER_RE.sub("", template)
    if "{{" in unmatched or "}}" in unmatched:
        raise ValueError("Wrapper prompt contains a malformed placeholder.")
    found = set(matches)
    missing = sorted(WRAPPER_PLACEHOLDERS - found)
    unknown = sorted(found - WRAPPER_PLACEHOLDERS)
    if missing:
        missing_text = ", ".join(f"{{{{{value}}}}}" for value in missing)
        raise ValueError(f"Wrapper prompt is missing placeholders: {missing_text}.")
    if unknown:
        unknown_text = ", ".join(f"{{{{{value}}}}}" for value in unknown)
        raise ValueError(f"Wrapper prompt contains unknown placeholders: {unknown_text}.")
    return template


def allocation_policy_text(prompt: Prompt) -> str:
    policy = allocation_policy_out(prompt)
    return "\n".join(
        [
            "- Invest exactly 100% across USD-denominated equities and ETFs.",
            (
                f"- Use between {policy['derived_min_positions']} and "
                f"{policy['derived_max_positions']} positions."
            ),
            (
                f"- Every position must be between {policy['min_position_weight_pct']:g}% and "
                f"{policy['max_position_weight_pct']:g}% of NAV."
            ),
            "- Do not use cash, mutual funds, options, futures, indices, FX, shorts, or leverage.",
            "- Validate every final symbol before submitting.",
        ]
    )


def render_execution_prompt(
    portfolio: Portfolio,
    wrapper_prompt: str,
    submission_instructions: str,
) -> str:
    """Render one wrapper in a single pass so inserted text is never re-expanded."""
    prompt = portfolio.prompt
    if prompt is None:
        raise ValueError("Benchmark portfolios do not have execution prompts")
    values = {
        "portfolio_slug": portfolio.slug,
        "strategy_text": prompt.text.strip(),
        "allocation_policy": allocation_policy_text(prompt),
        "submission_instructions": submission_instructions.strip(),
    }
    validated = validate_wrapper_prompt(wrapper_prompt)
    return _WRAPPER_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], validated).strip()


def manual_execution_prompt(portfolio: Portfolio, wrapper_prompt: str) -> str:
    """Build the complete prompt copied from a portfolio's public detail page."""
    return render_execution_prompt(portfolio, wrapper_prompt, MANUAL_SUBMISSION_INSTRUCTIONS)


def automated_execution_prompt(portfolio: Portfolio, wrapper_prompt: str) -> str:
    """Build the complete prompt sent to an integrated evaluator worker."""
    return render_execution_prompt(portfolio, wrapper_prompt, AUTOMATED_SUBMISSION_INSTRUCTIONS)
