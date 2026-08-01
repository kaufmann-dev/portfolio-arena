"""Mode- and direction-specific execution prompts and the frozen rebuilt strategy catalog."""

from types import SimpleNamespace

from app.services.prompt_policy import (
    DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
    DEFAULT_MANAGED_WRAPPER_PROMPT,
    DEFAULT_REBUILT_WRAPPER_PROMPT,
    DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
    V2_REBUILT_PROMPTS,
    automated_execution_prompt,
    manual_execution_prompt,
)


class _ModePrompt:
    mode = "both"
    direction = "both"
    managed_long_text = "Use the managed long strategy evidence."
    managed_short_text = "Use the managed short strategy evidence."
    rebuilt_long_text = "Use the rebuilt long strategy evidence."
    rebuilt_short_text = "Use the rebuilt short strategy evidence."

    def text_for(self, mode: str, direction: str) -> str:
        return getattr(self, f"{mode}_{direction}_text")


def _portfolio(prompt_mode: str, direction: str = "long"):
    return SimpleNamespace(
        slug=f"{prompt_mode}-strategy",
        prompt_mode=prompt_mode,
        direction=direction,
        prompt=_ModePrompt(),
    )


def test_managed_execution_instructions_remain_allocation_specific():
    portfolio = _portfolio("managed")
    policy = {
        "min_position_weight_pct": 10,
        "max_position_weight_pct": 25,
        "derived_min_positions": 4,
        "derived_max_positions": 10,
    }

    manual = manual_execution_prompt(
        portfolio,
        DEFAULT_MANAGED_WRAPPER_PROMPT,
        DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
        policy,
    )
    automated = automated_execution_prompt(
        portfolio,
        DEFAULT_MANAGED_WRAPPER_PROMPT,
        DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
        policy,
    )

    assert "call `create_allocation` exactly once" in manual
    assert "create_signal" not in manual
    assert "for a valid allocation" in automated
    assert "valid signal allocation" not in automated
    assert portfolio.prompt.managed_long_text in manual
    assert portfolio.prompt.rebuilt_long_text not in manual
    assert portfolio.prompt.managed_short_text not in manual
    assert portfolio.prompt.managed_long_text in automated
    assert portfolio.prompt.rebuilt_long_text not in automated
    assert portfolio.prompt.managed_short_text not in automated


def test_rebuilt_execution_instructions_are_signal_specific_and_stateless():
    portfolio = _portfolio("rebuilt")
    policy = {
        "min_position_weight_pct": 10,
        "max_position_weight_pct": 100,
        "derived_min_positions": 1,
        "derived_max_positions": 10,
    }

    manual = manual_execution_prompt(
        portfolio,
        DEFAULT_REBUILT_WRAPPER_PROMPT,
        DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
        policy,
    )
    automated = automated_execution_prompt(
        portfolio,
        DEFAULT_REBUILT_WRAPPER_PROMPT,
        DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
        policy,
    )

    assert "call `create_signal` exactly once" in manual
    assert "previous signals" in manual
    assert "valid signal allocation" in automated
    assert "create_allocation" not in manual
    assert portfolio.prompt.rebuilt_long_text in manual
    assert portfolio.prompt.managed_long_text not in manual
    assert portfolio.prompt.rebuilt_short_text not in manual
    assert portfolio.prompt.rebuilt_long_text in automated
    assert portfolio.prompt.managed_long_text not in automated
    assert portfolio.prompt.rebuilt_short_text not in automated
    assert "Subject to the allocation policy, a single security may receive 100%" in automated
    assert "Do not use prior portfolio state from any source" in automated


def test_short_execution_policy_uses_positive_weights_and_correct_benchmark_polarity():
    portfolio = _portfolio("managed", "short")
    policy = {
        "min_position_weight_pct": 10,
        "max_position_weight_pct": 25,
        "derived_min_positions": 4,
        "derived_max_positions": 10,
    }

    prompt = manual_execution_prompt(
        portfolio,
        DEFAULT_MANAGED_WRAPPER_PROMPT,
        DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
        policy,
    )

    assert "prices are expected to underperform SPY" in prompt
    assert "short book can outperform the Short SPY reference" in prompt
    assert "Submit positive weights totaling exactly 100%" in prompt
    assert "server interprets every position as gross short exposure" in prompt
    assert prompt.count("This is an all-short portfolio") == 1
    assert portfolio.prompt.managed_short_text in prompt
    assert portfolio.prompt.managed_long_text not in prompt


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
