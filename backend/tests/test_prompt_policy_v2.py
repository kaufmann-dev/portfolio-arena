"""Mode-specific execution prompts and the frozen rebuilt strategy catalog."""

from types import SimpleNamespace

from app.services.prompt_policy import (
    DEFAULT_MANAGED_WRAPPER_PROMPT,
    DEFAULT_REBUILT_WRAPPER_PROMPT,
    V2_REBUILT_PROMPTS,
    automated_execution_prompt,
    manual_execution_prompt,
)


def _portfolio(prompt_mode: str):
    return SimpleNamespace(
        slug=f"{prompt_mode}-strategy",
        prompt_mode=prompt_mode,
        prompt=SimpleNamespace(
            text="Use current, security-specific evidence.",
            min_position_weight_pct=10,
            max_position_weight_pct=25,
        ),
    )


def test_managed_execution_instructions_remain_allocation_specific():
    portfolio = _portfolio("managed")

    manual = manual_execution_prompt(portfolio, DEFAULT_MANAGED_WRAPPER_PROMPT)
    automated = automated_execution_prompt(portfolio, DEFAULT_MANAGED_WRAPPER_PROMPT)

    assert "call `create_allocation` exactly once" in manual
    assert "create_signal" not in manual
    assert "for a valid allocation" in automated
    assert "valid signal allocation" not in automated


def test_rebuilt_execution_instructions_are_signal_specific_and_stateless():
    portfolio = _portfolio("rebuilt")

    manual = manual_execution_prompt(portfolio, DEFAULT_REBUILT_WRAPPER_PROMPT)
    automated = automated_execution_prompt(portfolio, DEFAULT_REBUILT_WRAPPER_PROMPT)

    assert "call `create_signal` exactly once" in manual
    assert "previous signals" in manual
    assert "valid signal allocation" in automated
    assert "create_allocation" not in manual


def test_v2_rebuilt_catalog_rewrites_exactly_eight_weekly_strategies():
    assert {
        old_slug: (replacement["slug"], replacement["name"])
        for old_slug, replacement in V2_REBUILT_PROMPTS.items()
    } == {
        "barebones-weekly": ("unrestricted-selection", "Unrestricted Selection"),
        "weekly-economic-read-through": ("economic-read-through", "Economic Read-Through"),
        "weekly-fresh-information-repricing": (
            "fresh-information-repricing",
            "Fresh Information Repricing",
        ),
        "weekly-policy-and-geopolitical-anticipation": (
            "policy-and-geopolitical-anticipation",
            "Policy and Geopolitical Anticipation",
        ),
        "weekly-post-earnings-underreaction": (
            "post-earnings-underreaction",
            "Post-Earnings Underreaction",
        ),
        "weekly-pre-earnings-variant": ("pre-earnings-variant", "Pre-Earnings Variant"),
        "weekly-scheduled-catalysts": ("scheduled-catalysts", "Scheduled Catalysts"),
        "weekly-temporary-price-dislocation": (
            "temporary-price-dislocation",
            "Temporary Price Dislocation",
        ),
    }
    assert all(replacement["text"].strip() for replacement in V2_REBUILT_PROMPTS.values())
