"""Prompt allocation policy helpers and execution-wrapper rendering."""

import math
import re

from ..models import Portfolio, Prompt

PROMPT_MODES = {"managed", "rebuilt"}
PROMPT_VERSION_MODES = {"managed", "rebuilt", "both"}
WRAPPER_PLACEHOLDERS = {
    "portfolio_slug",
    "strategy_text",
    "allocation_policy",
    "submission_instructions",
}
_WRAPPER_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


def prompt_supports_mode(prompt_mode: str, version_mode: str) -> bool:
    if prompt_mode not in PROMPT_MODES:
        raise ValueError("Prompt mode must be 'managed' or 'rebuilt'.")
    if version_mode not in PROMPT_VERSION_MODES:
        raise ValueError("Prompt version mode must be 'managed', 'rebuilt', or 'both'.")
    return version_mode == "both" or version_mode == prompt_mode


def validate_prompt_texts(
    mode: str,
    managed_text: str | None,
    rebuilt_text: str | None,
) -> None:
    """Validate the exact text/null shape for one immutable prompt version."""
    if mode not in PROMPT_VERSION_MODES:
        raise ValueError("Prompt mode must be 'managed', 'rebuilt', or 'both'.")
    for prompt_mode, text in (
        ("managed", managed_text),
        ("rebuilt", rebuilt_text),
    ):
        supported = mode == "both" or mode == prompt_mode
        if supported and (text is None or not text.strip()):
            raise ValueError(f"{prompt_mode.title()} prompt text is required for mode '{mode}'.")
        if not supported and text is not None:
            raise ValueError(f"{prompt_mode.title()} prompt text must be null for mode '{mode}'.")


DEFAULT_MANAGED_WRAPPER_PROMPT = """\
Evaluate the Portfolio Arena portfolio `{{portfolio_slug}}` and produce its next allocation.

Call the Portfolio Arena `get_portfolio` tool first. Treat its prompt mode, strategy, allocation
policy, current holdings, notes, allocation history, performance, and effective date as authoritative.

Act as a US equity portfolio manager aiming to outperform the portfolio's direction-matched SPY
reference. Search across the full eligible US market rather than defaulting to index constituents,
household names, or recent winners. Do not mirror SPY. Select a stock or ETF only when it has a
distinct, falsifiable, security-specific investment thesis supported by current evidence.

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

MANAGED_AUTOMATED_SUBMISSION_INSTRUCTIONS = """\
Do not call any write tool: the worker will validate and submit the final structured proposal
atomically.

The report should briefly explain the portfolio-level decision, key evidence, material risks, and
what would change the next evaluation. Position notes should be concise handoff context.

Return `status` as `proposal` with an empty `error` for a valid allocation. If `get_portfolio` fails
or no valid allocation can be produced, return `status` as `blocked`, no positions, and a concise
`error`; never invent a placeholder symbol."""

REBUILT_AUTOMATED_SUBMISSION_INSTRUCTIONS = """\
Do not call any write tool: the worker will validate and submit the final structured proposal
atomically.

The report should briefly explain the signal-level decision, key evidence, material risks, and what
would change the next evaluation. Position notes should be concise signal context.

Return `status` as `proposal` with an empty `error` for a valid signal allocation. If `get_portfolio`
fails or no valid signal allocation can be produced, return `status` as `blocked`, no positions, and
a concise `error`; never invent a placeholder symbol."""

MANAGED_MANUAL_SUBMISSION_INSTRUCTIONS = """\
When the analysis is complete, call `create_allocation` exactly once with the portfolio id returned by
`get_portfolio`. Include a concise portfolio-level note and useful per-position notes so the next
evaluation can understand the decision."""

REBUILT_MANUAL_SUBMISSION_INSTRUCTIONS = """\
When the analysis is complete, call `create_signal` exactly once with the portfolio id returned by
`get_portfolio`. Include a concise portfolio-level note and useful per-position notes that explain
the independent signal."""

V2_REBUILT_PROMPTS = {
    "barebones-weekly": {
        "slug": "unrestricted-selection",
        "name": "Unrestricted Selection",
        "text": """\
Select the strongest evidence-backed opportunities for expected total return relative to SPY without
restricting the source of the edge.

For every candidate, identify the new, changing, or underprocessed fact; the market's apparent current
expectation; why that expectation is wrong; the causal path from the fact to earnings, cash flow,
valuation, or capital returns; why the gap remains unpriced; and the strongest contrary evidence and
downside. Rank candidates comparatively using quantitative evidence wherever possible.

Reject generic claims that a company is great, cheap, exposed to AI, oversold, or showing momentum
unless a specific and falsifiable mispricing explains why it should outperform. Avoid hidden
concentration in the same economic dependency across different securities.""",
    },
    "weekly-economic-read-through": {
        "slug": "economic-read-through",
        "name": "Economic Read-Through",
        "text": """\
Select opportunities where fresh external economic, industry, supply-chain, demand, pricing, or
competitive evidence has a direct and underprocessed implication for a specific security.

State the full causal chain from the external evidence to the company's exposure, financial impact,
and expected repricing. Verify the exposure's size, contract structure, hedges, and reporting lags,
and explain why the market has not yet incorporated the implication.

Reject broad themes, stale correlations, indirect or immaterial exposures, and signals neutralized by
hedging, contract terms, mix, or an offsetting business effect.""",
    },
    "weekly-fresh-information-repricing": {
        "slug": "fresh-information-repricing",
        "name": "Fresh Information Repricing",
        "text": """\
Select opportunities driven by newly public, material company-specific information that the market
has not fully processed.

Identify the publication and timestamp, the assumption or economic outcome it changes, why the
information remains underprocessed, and which audience or recognition process should close the gap.
Test the strongest alternative interpretation and distinguish new information from a restatement of
what investors already knew.

Reject stale, immaterial, speculative, fully priced, or causally remote information.""",
    },
    "weekly-policy-and-geopolitical-anticipation": {
        "slug": "policy-and-geopolitical-anticipation",
        "name": "Policy and Geopolitical Anticipation",
        "text": """\
Select opportunities where observable policy or geopolitical developments create a differentiated,
evidence-backed expectation for a specific security.

Start with the concrete event. Analyze the relevant actors, incentives, constraints, precursors,
credible alternative outcomes, and the market's apparent expectation. Separate the probability of
the event from the security's likely reaction, then map the transmission mechanism and show why the
financial exposure is direct, material, and asymmetric.

Reject rumors, rhetoric without implementation evidence, generic thematic baskets, and broad outcome
guesses without a security-specific valuation gap.""",
    },
    "weekly-post-earnings-underreaction": {
        "slug": "post-earnings-underreaction",
        "name": "Post-Earnings Underreaction",
        "text": """\
Select opportunities where a recent earnings release contains durable information that the market
has not fully incorporated.

Review the complete earnings materials rather than the headline. Identify the change in underlying
economics, its quality and durability, what the market emphasized, the facts it underprocessed, and
the recognition mechanism that can close the gap. Distinguish recurring operating evidence from
one-offs, accounting effects, and temporary timing.

Reject simple earnings-beat buying, headline or price momentum, low-quality surprises, and results
whose implications are already reflected in expectations and valuation.""",
    },
    "weekly-pre-earnings-variant": {
        "slug": "pre-earnings-variant",
        "name": "Pre-Earnings Variant",
        "text": """\
Select opportunities with a confirmed upcoming earnings report where a differentiated, testable
expectation creates favorable asymmetry.

Reconstruct the true market bar from consensus estimates, guidance, segment assumptions, valuation,
and current channel or operating evidence. State the differentiated assumption, the metric most
likely to reveal it, how investors are likely to interpret the result, and the downside if the view
is wrong.

Reject routine previews, unknowable binary bets, crowded variants, and setups whose plausible upside
does not compensate for the downside.""",
    },
    "weekly-scheduled-catalysts": {
        "slug": "scheduled-catalysts",
        "name": "Scheduled Catalysts",
        "text": """\
Select opportunities with a confirmed, dated, non-earnings catalyst that can materially change a
security's expected economics or valuation.

Identify the event and date, decision makers, prerequisites, plausible outcomes, priced expectation,
security-specific consequence, and downside. Prefer primary documentation and verify that the event
is actually scheduled and that its impact is not already reflected in price.

Reject rumors, undated possibilities, immaterial events, catalysts without an expectation gap, and
binary setups with unattractive asymmetry.""",
    },
    "weekly-temporary-price-dislocation": {
        "slug": "temporary-price-dislocation",
        "name": "Temporary Price Dislocation",
        "text": """\
Select opportunities where forced, mechanical, or economically indifferent trading—or an excessive
reaction to a temporary event—has moved price away from a defensible value anchor.

Identify the source and trigger of the selling, its likely scale and duration, the value anchor, the
natural buyer or correction mechanism, and clear invalidation evidence. Demonstrate that the
underlying impairment is temporary rather than structural.

Reject squeeze theses, short-interest stories, generic oversold claims, falling knives, unresolved
accounting or financing risk, permanently impaired liquidity, and price declines justified by a
lasting deterioration in business value.""",
    },
}


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


def allocation_policy_text(prompt: Prompt, direction: str) -> str:
    policy = allocation_policy_out(prompt)
    if direction not in {"long", "short"}:
        raise ValueError("Portfolio direction must be long or short")
    direction_rules = (
        [
            "- This is an all-long portfolio. Every submitted position is a long position.",
            "- Invest exactly 100% of NAV across USD-denominated equities and ETFs.",
            "- Do not use cash, shorts, or leverage.",
        ]
        if direction == "long"
        else [
            (
                "- This is an all-short portfolio. Select securities whose prices are expected to "
                "underperform SPY so the short book can outperform the Short SPY reference."
            ),
            (
                "- Submit positive weights totaling exactly 100%; the server interprets every "
                "position as gross short exposure."
            ),
            "- Do not use cash, long positions, or gross exposure above 100%.",
        ]
    )
    return "\n".join(
        [
            *direction_rules,
            (
                f"- Use between {policy['derived_min_positions']} and "
                f"{policy['derived_max_positions']} positions."
            ),
            (
                f"- Every position must be between {policy['min_position_weight_pct']:g}% and "
                f"{policy['max_position_weight_pct']:g}% of NAV."
            ),
            "- Do not use mutual funds, options, futures, indices, or FX.",
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
        raise ValueError("Portfolio does not have an execution prompt")
    values = {
        "portfolio_slug": portfolio.slug,
        "strategy_text": prompt.text_for_mode(portfolio.prompt_mode).strip(),
        "allocation_policy": allocation_policy_text(prompt, portfolio.direction),
        "submission_instructions": submission_instructions.strip(),
    }
    validated = validate_wrapper_prompt(wrapper_prompt)
    return _WRAPPER_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], validated).strip()


def manual_execution_prompt(portfolio: Portfolio, wrapper_prompt: str) -> str:
    """Build the complete prompt copied from a portfolio's public detail page."""
    submission_instructions = (
        REBUILT_MANUAL_SUBMISSION_INSTRUCTIONS
        if portfolio.prompt_mode == "rebuilt"
        else MANAGED_MANUAL_SUBMISSION_INSTRUCTIONS
    )
    return render_execution_prompt(portfolio, wrapper_prompt, submission_instructions)


def automated_execution_prompt(portfolio: Portfolio, wrapper_prompt: str) -> str:
    """Build the complete prompt sent to an integrated evaluator worker."""
    submission_instructions = (
        REBUILT_AUTOMATED_SUBMISSION_INSTRUCTIONS
        if portfolio.prompt_mode == "rebuilt"
        else MANAGED_AUTOMATED_SUBMISSION_INSTRUCTIONS
    )
    return render_execution_prompt(portfolio, wrapper_prompt, submission_instructions)
