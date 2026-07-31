"""Focused deterministic tests for the v2 rebuilt signal engine."""

import math
from types import SimpleNamespace

import pytest

from app.services.arena import (
    RebuiltPortfolioAnalysis,
    _common_candidate_data,
    _common_candidate_metrics,
    _rankable_common_members,
)
from app.services.rebuilt import (
    DIRECT_SEARCH_FAMILY_SIZE,
    OPTIMIZED_SEARCH_FAMILY_SIZE,
    PolicyResult,
    SignalInput,
    construct_policy,
    evaluate_policy_grid,
    hac_mean_statistics,
    policy_metrics,
    select_policy,
    signal_horizon_statistics,
)
from app.services.valuation import PositionInput, ValuationResult, compute_metrics


def prices(symbol, days, values):
    return {symbol: [{"date": day, "close": value} for day, value in zip(days, values, strict=True)]}


DAYS = [
    "2026-01-05",
    "2026-01-06",
    "2026-01-07",
    "2026-01-08",
    "2026-01-09",
    "2026-01-12",
]


def signal(identifier, day, symbol="AAPL"):
    return SignalInput(
        id=identifier,
        effective_date=day,
        positions=(PositionInput(symbol=symbol, weight_pct=100.0),),
    )


def market(aapl=None, spy=None, msft=None):
    data = {}
    data.update(prices("AAPL", DAYS, aapl or [100.0] * len(DAYS)))
    data.update(prices("SPY", DAYS, spy or [100.0] * len(DAYS)))
    if msft is not None:
        data.update(prices("MSFT", DAYS, msft))
    return data


def test_horizon_is_exact_close_to_close_interval_and_then_exits_to_spy():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(aapl=[100, 110, 121, 200, 200, 200]),
        DAYS,
        horizon=2,
        exposure_pct=100,
        cost_bps=0,
        cost_basis="gross",
    )

    # One H=2 cohort supplies 50% exposure for exactly the first two returns.
    assert result.daily_returns[0]["return"] == pytest.approx(0.05)
    assert result.daily_returns[1]["return"] == pytest.approx(0.05)
    assert result.daily_returns[2]["return"] == pytest.approx(0.0)
    assert result.holdings == [{"symbol": "SPY", "weight_pct": 100.0}]


def test_twenty_day_policy_caps_each_daily_cohort_at_five_percent():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(),
        DAYS,
        horizon=20,
        exposure_pct=100,
        cost_bps=0,
        cost_basis="gross",
    )

    assert result.series[0]["nav"] == pytest.approx(100.0)
    assert result.holdings == [
        {"symbol": "AAPL", "weight_pct": 5.0},
        {"symbol": "SPY", "weight_pct": 95.0},
    ]


def test_net_performance_retains_initial_entry_turnover_cost():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(),
        DAYS,
        horizon=1,
        exposure_pct=100,
        cost_bps=10,
        cost_basis="net",
    )
    metrics = policy_metrics(
        result,
        {
            "complete_count": 2,
            "open_count": 0,
            "completion_ratio": 1.0,
            "eligible": True,
        },
    )

    # SPY -> AAPL is 100% one-sided turnover; both legs pay 10 bps.
    assert result.series[0]["nav"] == pytest.approx(99.8)
    # The H=1 cohort exits back to SPY at the next close, charging the reverse trade.
    assert result.cumulative_cost == pytest.approx(0.3996)
    assert result.daily_returns[0]["cost"] == pytest.approx(0.3996)
    assert result.daily_returns[0]["return"] == pytest.approx(result.series[1]["nav"] / 100.0 - 1.0)
    compounded = math.prod(1.0 + point["return"] for point in result.daily_returns) - 1.0
    assert compounded == pytest.approx(metrics["itd_return"])
    assert metrics["itd_return"] < 0
    assert metrics["mean_daily_alpha"] < 0
    assert metrics["sharpe"] < 0
    assert metrics["information_ratio"] < 0
    assert metrics["max_drawdown"] < 0


def test_daily_returns_compound_to_itd_with_price_moves_and_rebalances():
    result = construct_policy(
        [signal(1, DAYS[0]), signal(2, DAYS[2])],
        market(
            aapl=[100, 104, 103, 108, 106, 111],
            spy=[100, 101, 100, 102, 103, 105],
        ),
        DAYS,
        horizon=2,
        exposure_pct=70,
        cost_bps=25,
        cost_basis="net",
    )
    metrics = policy_metrics(
        result,
        {
            "complete_count": 2,
            "open_count": 0,
            "completion_ratio": 1.0,
            "eligible": True,
        },
    )

    compounded = math.prod(1.0 + point["return"] for point in result.daily_returns) - 1.0
    assert compounded == pytest.approx(metrics["itd_return"])


def test_direct_alpha_uses_geometric_daily_excess_and_pending_until_eligible():
    stats = signal_horizon_statistics(
        [signal(1, DAYS[0])],
        market(
            aapl=[100, 100, 121, 121, 121, 121],
            spy=[100, 100, 110, 110, 110, 110],
        ),
        DAYS,
        horizon=2,
    )

    expected = (1.21 / 1.10) ** 0.5 - 1.0
    assert stats["mean_daily_alpha"] == pytest.approx(expected)
    assert stats["complete_count"] == 1
    assert stats["eligible"] is False
    assert stats["evidence"] == "pending"


def test_completion_ratio_must_reach_half_with_two_completed_cohorts():
    stats = signal_horizon_statistics(
        [
            signal(1, DAYS[0]),
            signal(2, DAYS[1]),
            signal(3, DAYS[4]),
            signal(4, DAYS[5]),
        ],
        market(),
        DAYS,
        horizon=2,
    )

    assert stats["complete_count"] == 2
    assert stats["open_count"] == 2
    assert stats["completion_ratio"] == pytest.approx(0.5)
    assert stats["eligible"] is True


def test_future_effective_signal_counts_as_open_evidence():
    stats = signal_horizon_statistics(
        [signal(1, "2026-02-02")],
        market(),
        DAYS,
        horizon=1,
    )

    assert stats["complete_count"] == 0
    assert stats["open_count"] == 1
    assert stats["completion_ratio"] == 0
    assert stats["evidence"] == "pending"


def test_multiple_signals_on_one_actual_session_split_one_cohort_sleeve():
    result = construct_policy(
        [
            signal(1, "2026-01-03", "AAPL"),
            signal(2, "2026-01-04", "MSFT"),
        ],
        market(msft=[100.0] * len(DAYS)),
        DAYS,
        horizon=20,
        exposure_pct=100,
        cost_bps=0,
        cost_basis="gross",
    )

    assert result.holdings == [
        {"symbol": "AAPL", "weight_pct": 2.5},
        {"symbol": "MSFT", "weight_pct": 2.5},
        {"symbol": "SPY", "weight_pct": 95.0},
    ]
    assert sum(holding["weight_pct"] for holding in result.holdings) == pytest.approx(100)
    assert len(result.active_cohorts) == 2


def test_ineligible_direct_and_policy_stats_hide_confidence_interval():
    direct = signal_horizon_statistics(
        [
            signal(1, DAYS[0]),
            signal(2, DAYS[1]),
            signal(3, DAYS[4]),
            signal(4, DAYS[5]),
            signal(5, "2026-02-02"),
        ],
        market(),
        DAYS,
        horizon=2,
        family_size=20,
    )
    assert direct["complete_count"] == 2
    assert direct["open_count"] == 3
    assert direct["mean_daily_alpha"] == pytest.approx(0)
    assert direct["ci_lower"] is None
    assert direct["ci_upper"] is None
    assert direct["evidence"] == "pending"

    result = construct_policy(
        [signal(1, DAYS[0]), signal(2, DAYS[1])],
        market(),
        DAYS,
        horizon=2,
        exposure_pct=100,
        cost_bps=10,
        cost_basis="net",
    )
    metrics = policy_metrics(
        result,
        {
            "complete_count": 2,
            "open_count": 3,
            "completion_ratio": 0.4,
            "eligible": False,
        },
        family_size=20,
    )
    assert metrics["mean_daily_alpha"] is not None
    assert metrics["ci_lower"] is None
    assert metrics["ci_upper"] is None
    assert metrics["evidence"] == "pending"


def test_policy_grid_uses_fixed_search_families():
    signals = [signal(1, DAYS[0]), signal(2, DAYS[1])]

    horizons, canonical, _ = evaluate_policy_grid(
        signals,
        market(),
        DAYS,
        cost_bps=0,
        cost_basis="gross",
        objective="canonical",
    )
    _, optimized, _ = evaluate_policy_grid(
        signals,
        market(),
        DAYS,
        cost_bps=0,
        cost_basis="gross",
        objective="max_alpha",
    )

    assert DIRECT_SEARCH_FAMILY_SIZE == 20
    assert OPTIMIZED_SEARCH_FAMILY_SIZE == 200
    assert {item["family_size"] for item in horizons} == {20}
    assert {item.metrics["family_size"] for item in canonical} == {20}
    assert {item.metrics["family_size"] for item in optimized} == {200}


def test_bonferroni_search_adjustment_widens_hac_interval():
    values = [0.01, -0.005, 0.012, -0.002, 0.008, 0.004]

    single = hac_mean_statistics(values, lag=2, family_size=1)
    searched = hac_mean_statistics(values, lag=2, family_size=200)

    assert searched["ci_lower"] < single["ci_lower"]
    assert searched["ci_upper"] > single["ci_upper"]


def test_one_hac_observation_is_descriptive_but_never_rankable():
    stats = hac_mean_statistics([0.01], lag=0)

    assert stats["mean_daily_alpha"] == pytest.approx(0.01)
    assert stats["hac_standard_error"] is None
    assert stats["ci_lower"] is None
    assert stats["ci_upper"] is None
    assert stats["evidence"] == "pending"


def test_managed_single_daily_return_remains_pending():
    result = ValuationResult(
        series=[
            {"date": DAYS[0], "nav": 100.0},
            {"date": DAYS[1], "nav": 102.0},
        ],
        allocations=[],
        holdings=[],
    )
    spy = [
        {"date": DAYS[0], "close": 100.0},
        {"date": DAYS[1], "close": 101.0},
    ]

    metrics = compute_metrics(result, spy)

    assert metrics["mean_daily_alpha"] == pytest.approx(0.01)
    assert metrics["ci_lower"] is None
    assert metrics["evidence"] == "pending"


def test_policy_selection_ties_choose_lower_exposure_then_shorter_horizon():
    candidates = []
    for horizon, exposure in ((2, 20), (1, 20), (1, 10)):
        item = PolicyResult(
            horizon=horizon,
            exposure_pct=exposure,
            cost_basis="net",
            series=[],
            spy_series=[],
            daily_returns=[],
            holdings=[],
            active_cohorts=[],
            cumulative_cost=0.0,
            cumulative_turnover_pct=0.0,
            metrics={"eligible": True, "mean_daily_alpha": 0.01},
        )
        candidates.append(item)

    selected = select_policy(candidates, "max_alpha")

    assert selected is not None
    assert (selected.horizon, selected.exposure_pct) == (1, 10)


def _analysis(identifier, daily_returns):
    policy = PolicyResult(
        horizon=1,
        exposure_pct=100,
        cost_basis="net",
        series=[],
        spy_series=[],
        daily_returns=daily_returns,
        holdings=[],
        active_cohorts=[],
        cumulative_cost=0.0,
        cumulative_turnover_pct=0.0,
        metrics={"eligible": True},
    )
    return RebuiltPortfolioAnalysis(
        portfolio=SimpleNamespace(id=identifier),
        signal_horizons=[],
        policies={(1, 100): policy},
        selected=policy,
    )


def test_common_meta_uses_fixed_members_shared_dates_and_spy_for_missing_observation():
    first = _analysis(
        1,
        [
            {"date": DAYS[1], "return": 0.02, "spy_return": 0.01},
            {"date": DAYS[2], "return": 0.03, "spy_return": 0.01},
        ],
    )
    second = _analysis(
        2,
        [
            # No observation on DAYS[1]: this admitted member must contribute SPY.
            {"date": DAYS[2], "return": -0.01, "spy_return": 0.01},
        ],
    )

    result = _common_candidate_metrics(
        [first, second],
        horizon=1,
        exposure=100,
        family_size=1,
        dates=[DAYS[1], DAYS[2]],
        baseline=DAYS[0],
    )

    assert result is not None
    meta, members = result
    # Day 1 alpha = average(+1%, 0%); day 2 = average(+2%, -2%).
    assert meta["mean_daily_alpha"] == pytest.approx(0.0025)
    assert members[2]["start_date"] == DAYS[0]
    assert members[2]["observation_count"] == 2


def test_common_member_metrics_stop_at_liquidation_while_display_series_stays_zero():
    liquidated = _analysis(
        1,
        [
            {
                "date": DAYS[1],
                "return": -1.0,
                "spy_return": 0.01,
                "turnover_pct": 0.0,
                "cost": 0.0,
            },
        ],
    )
    liquidated.policies[(1, 100)].liquidated_at = DAYS[1]
    surviving = _analysis(
        2,
        [
            {"date": DAYS[1], "return": 0.02, "spy_return": 0.01},
            {"date": DAYS[2], "return": 0.03, "spy_return": 0.01},
        ],
    )

    result = _common_candidate_data(
        [liquidated, surviving],
        horizon=1,
        exposure=100,
        family_size=1,
        dates=[DAYS[1], DAYS[2]],
        baseline=DAYS[0],
    )

    assert result is not None
    assert result.member_metrics[1]["observation_count"] == 1
    assert [point["nav"] for point in result.member_series[1]] == [100.0, 0.0, 0.0]


def test_common_excludes_failed_analysis_without_hiding_its_arena_row():
    failed = RebuiltPortfolioAnalysis(
        portfolio=SimpleNamespace(
            id=9,
            status="active",
            founding_v2=True,
            direction="long",
        ),
        signal_horizons=[],
        policies={},
        selected=None,
        error="missing price",
    )

    assert _rankable_common_members({9: failed}, "long") == []


def test_explicit_long_policy_direction_is_identical_to_default_path():
    arguments = {
        "signals": [signal(1, DAYS[0]), signal(2, DAYS[2])],
        "prices": market(
            aapl=[100, 104, 103, 108, 106, 111],
            spy=[100, 101, 100, 102, 103, 105],
        ),
        "calendar": DAYS,
        "horizon": 2,
        "exposure_pct": 70,
        "cost_bps": 25,
        "cost_basis": "net",
    }

    default = construct_policy(**arguments)
    explicit = construct_policy(**arguments, direction="long")

    assert explicit == default


def test_short_policy_applies_inverse_pnl_to_signal_and_spy_residual():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(
            aapl=[100, 110, 110, 110, 110, 110],
            spy=[100, 102, 102, 102, 102, 102],
        ),
        DAYS,
        horizon=2,
        exposure_pct=50,
        cost_bps=0,
        cost_basis="gross",
        direction="short",
    )

    # H=2 allocates 25% to the active signal and leaves 75% in short SPY.
    assert result.daily_returns[0]["return"] == pytest.approx(-(0.25 * 0.10 + 0.75 * 0.02))
    assert result.direction == "short"


def test_short_policy_spy_series_compounds_negative_daily_spy_returns():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(spy=[100, 110, 99, 99, 99, 99]),
        DAYS,
        horizon=2,
        exposure_pct=100,
        cost_bps=0,
        cost_basis="gross",
        direction="short",
    )

    assert result.spy_series[2]["nav"] == pytest.approx(100.0 * 0.9 * 1.1)


def test_short_direct_alpha_uses_inverse_signal_and_compounded_short_spy():
    stats = signal_horizon_statistics(
        [signal(1, DAYS[0])],
        market(
            aapl=[100, 100, 121, 121, 121, 121],
            spy=[100, 100, 110, 110, 110, 110],
        ),
        DAYS,
        horizon=2,
        direction="short",
    )

    expected = (0.79 / 0.90) ** 0.5 - 1.0
    completed = stats["completed_cohorts"][0]
    assert completed["signal_return"] == pytest.approx(-0.21)
    assert completed["spy_return"] == pytest.approx(-0.10)
    assert completed["daily_alpha"] == pytest.approx(expected)


def test_short_direct_candidate_liquidates_but_remains_completed_evidence():
    signals = [signal(1, DAYS[0])]
    prices = market(
        aapl=[100, 210, 220, 220, 220, 220],
        # Short SPY survives through the candidate's liquidation, then
        # liquidates before the originally planned H=2 endpoint.
        spy=[100, 90, 210, 210, 210, 210],
    )
    stats = signal_horizon_statistics(
        signals,
        prices,
        DAYS,
        horizon=2,
        direction="short",
    )

    completed = stats["completed_cohorts"][0]
    assert stats["complete_count"] == 1
    assert stats["invalid_count"] == 0
    assert completed["signal_return"] == -1.0
    assert completed["daily_alpha"] == -1.0
    assert completed["liquidated_at"] == DAYS[1]
    assert completed["end_date"] == DAYS[1]
    assert completed["spy_return"] == pytest.approx(0.1)

    # Liquidation is terminal evidence even while the requested H=20 endpoint
    # is still in the future.
    horizon_twenty = signal_horizon_statistics(
        signals,
        prices,
        DAYS,
        horizon=20,
        direction="short",
    )
    assert horizon_twenty["complete_count"] == 1
    assert horizon_twenty["open_count"] == 0
    assert horizon_twenty["completed_cohorts"][0]["end_date"] == DAYS[1]


def test_short_direct_signal_without_an_observed_interval_remains_open():
    stats = signal_horizon_statistics(
        [signal(1, DAYS[-1])],
        market(
            aapl=[100, 100, 100, 100, 100, 100],
            spy=[100, 100, 100, 100, 100, 100],
        ),
        DAYS,
        horizon=20,
        direction="short",
    )

    assert stats["complete_count"] == 0
    assert stats["open_count"] == 1
    assert stats["invalid_count"] == 0


def test_short_direct_alpha_is_invalid_when_short_spy_liquidates():
    stats = signal_horizon_statistics(
        [signal(1, DAYS[0])],
        market(
            aapl=[100, 100, 100, 100, 100, 100],
            spy=[100, 210, 210, 210, 210, 210],
        ),
        DAYS,
        horizon=1,
        direction="short",
    )

    assert stats["complete_count"] == 0
    assert stats["invalid_count"] == 1


def test_short_policy_liquidation_extends_zero_series_without_post_liquidation_returns():
    result = construct_policy(
        [signal(1, DAYS[0])],
        market(
            aapl=[100, 210, 220, 230, 240, 250],
            spy=[100, 100, 100, 100, 100, 100],
        ),
        DAYS,
        horizon=1,
        exposure_pct=100,
        cost_bps=0,
        cost_basis="gross",
        direction="short",
    )

    assert result.liquidated_at == DAYS[1]
    assert result.series[1:] == [{"date": day, "nav": 0.0} for day in DAYS[1:]]
    assert result.daily_returns == [
        {
            "date": DAYS[1],
            "return": -1.0,
            "spy_return": 0.0,
            "alpha": -1.0,
            "turnover_pct": 100.0,
            "cost": 0.0,
        }
    ]
    assert result.holdings == []


def test_short_founding_portfolio_still_requires_eligible_horizon_twenty_for_common():
    policy = PolicyResult(
        horizon=1,
        exposure_pct=100,
        cost_basis="net",
        series=[],
        spy_series=[],
        daily_returns=[],
        holdings=[],
        active_cohorts=[],
        cumulative_cost=0.0,
        cumulative_turnover_pct=0.0,
    )
    policies = {(horizon, exposure): policy for horizon in range(1, 21) for exposure in range(10, 101, 10)}
    signal_horizons = [{"horizon": horizon, "eligible": horizon != 20} for horizon in range(1, 21)]
    long = RebuiltPortfolioAnalysis(
        portfolio=SimpleNamespace(
            id=1,
            status="active",
            founding_v2=True,
            direction="long",
        ),
        signal_horizons=signal_horizons,
        policies=policies,
        selected=policy,
    )
    short = RebuiltPortfolioAnalysis(
        portfolio=SimpleNamespace(
            id=2,
            status="active",
            founding_v2=True,
            direction="short",
        ),
        signal_horizons=signal_horizons,
        policies=policies,
        selected=policy,
    )

    assert _rankable_common_members({1: long, 2: short}, "long") == [long]
    assert _rankable_common_members({1: long, 2: short}, "short") == []
