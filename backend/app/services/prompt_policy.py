"""Prompt allocation policy helpers and the manual-agent handoff prompt."""

import math

from ..models import Portfolio, Prompt


def allocation_policy_out(prompt: Prompt) -> dict:
    minimum = float(prompt.min_position_weight_pct)
    maximum = float(prompt.max_position_weight_pct)
    return {
        "min_position_weight_pct": minimum,
        "max_position_weight_pct": maximum,
        "derived_min_positions": math.ceil(100 / maximum),
        "derived_max_positions": math.floor(100 / minimum),
    }


def validate_position_weights(prompt: Prompt, positions: list[dict]) -> None:
    """Raise ``ValueError`` when a position violates the prompt's active policy."""
    policy = allocation_policy_out(prompt)
    minimum = policy["min_position_weight_pct"]
    maximum = policy["max_position_weight_pct"]
    for position in positions:
        weight = float(position["weight_pct"])
        if weight < minimum or weight > maximum:
            raise ValueError(f"{position['symbol']} weight must be between {minimum:g}% and {maximum:g}%.")


def manual_execution_prompt(portfolio: Portfolio) -> str:
    """Build the complete prompt copied from a portfolio's public detail page."""
    policy = allocation_policy_out(portfolio.prompt)
    return f"""Evaluate and rebalance the Portfolio Arena portfolio `{portfolio.slug}`.

First call `get_portfolio` with `{portfolio.slug}`. Treat its current holdings, allocation history,
notes, effective date, and performance as the authoritative state. Manage the existing portfolio;
do not rebuild it from scratch.

Strategy:
{portfolio.prompt.text.strip()}

Allocation policy:
- Invest exactly 100% across USD-denominated equities and ETFs.
- Use between {policy["derived_min_positions"]} and {policy["derived_max_positions"]} positions.
- Every position must be between {policy["min_position_weight_pct"]:g}% and
  {policy["max_position_weight_pct"]:g}% of NAV.
- Do not use cash, mutual funds, options, futures, indices, FX, short positions, or leverage.
- Validate unfamiliar symbols before submitting.

When your analysis is complete, call `create_allocation` exactly once with the portfolio id from
`get_portfolio`. Include a concise portfolio-level note and useful per-position notes so the next
evaluation can understand this decision.
"""
