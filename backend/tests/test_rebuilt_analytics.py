"""Hand-calculated canonical half-session signal and portfolio analytics."""

from datetime import date, timedelta

import pytest

from app.services.rebuilt import (
    HORIZONS,
    PolicyResult,
    SignalInput,
    construct_policy,
    evaluate_policy_grid,
    hac_mean_statistics,
    select_policy,
    signal_horizon_statistics,
)
from app.services.trading_calendar import boundary_value, is_trading_day
from app.services.valuation import PositionInput, ValuationError, build_calendar


def market(count=6):
    days = []
    day = date(2026, 1, 5)
    while len(days) < count:
        if is_trading_day(day):
            days.append(day.isoformat())
        day += timedelta(days=1)
    data = {
        symbol: [{"date": day, "open": 100.0, "close": 100.0} for day in days]
        for symbol in ("SPY", "AAPL", "MSFT")
    }
    return days, data, build_calendar(data["SPY"], boundary_value(date.fromisoformat(days[-1]), "close"))


def signal(identifier, day, symbol="AAPL"):
    return SignalInput(identifier, day, (PositionInput(symbol, 100),))


@pytest.mark.parametrize(
    "phase,horizon,end_index",
    [
        ("open", 0.5, 1),
        ("open", 1, 2),
        ("open", 1.5, 3),
        ("close", 0.5, 2),
        ("close", 1, 3),
        ("close", 1.5, 4),
    ],
)
def test_exact_half_boundary_horizons(phase, horizon, end_index):
    days, data, calendar = market()
    result = signal_horizon_statistics(
        [signal(1, days[0])], data, calendar, horizon, execution_boundary=phase
    )
    assert result["completed_cohorts"][0]["end_at"] == calendar[end_index]


def test_h20_skips_weekends_and_holidays():
    days, data, calendar = market(23)
    result = signal_horizon_statistics([signal(1, days[0])], data, calendar, 20)
    assert result["completed_cohorts"][0]["end_at"] == calendar[41]
    assert result["completed_cohorts"][0]["end_at"]["timestamp"][:10] == days[20]


def test_half_day_policy_exits_to_spy_at_close():
    days, data, calendar = market()
    data["AAPL"][0]["close"] = 110
    data["AAPL"][1]["open"] = 200
    result = construct_policy([signal(1, days[0])], data, calendar, 0.5, execution_boundary="open")
    assert [point["nav"] for point in result.series[:3]] == pytest.approx([100, 110, 110])
    assert result.holdings == [{"symbol": "SPY", "weight_pct": 100}]


def test_fractional_horizon_uses_ceiling_sleeves_without_leverage():
    days, data, calendar = market()
    data["AAPL"][0]["close"] = 120
    data["AAPL"][1]["open"] = 140
    data["AAPL"][1]["close"] = 160
    result = construct_policy([signal(1, days[0])], data, calendar, 1.5, execution_boundary="open")
    assert [point["nav"] for point in result.series[:4]] == pytest.approx([100, 110, 120, 130])
    assert result.series[-1]["nav"] == pytest.approx(130)


def test_additional_marks_do_not_rebalance_active_cohort():
    days, data, calendar = market()
    data["AAPL"][0]["close"] = 120
    data["AAPL"][1]["open"] = 140
    result = construct_policy([signal(1, days[0])], data, calendar[:3], 2, execution_boundary="open")
    assert [point["nav"] for point in result.series] == pytest.approx([100, 110, 120])
    assert result.cumulative_turnover_pct == pytest.approx(50)


def test_overlapping_entries_split_target_into_two_sleeves():
    days, data, calendar = market()
    result = construct_policy(
        [signal(1, days[0]), signal(2, days[1], "MSFT")], data, calendar[:4], 2, execution_boundary="open"
    )
    assert result.holdings == [{"symbol": "AAPL", "weight_pct": 50}, {"symbol": "MSFT", "weight_pct": 50}]
    assert len(result.active_cohorts) == 2


def test_duplicate_same_boundary_signals_share_one_sleeve():
    days, data, calendar = market()
    result = construct_policy(
        [signal(1, days[0]), signal(2, days[0], "MSFT")], data, calendar[:2], 1, execution_boundary="open"
    )
    assert result.holdings == [{"symbol": "AAPL", "weight_pct": 50}, {"symbol": "MSFT", "weight_pct": 50}]


def test_short_reference_sleeve_follows_daily_close_reset():
    days, data, calendar = market()
    data["SPY"][0]["close"] = 110
    data["SPY"][1]["open"] = 121
    data["SPY"][1]["close"] = 132
    result = construct_policy(
        [signal(1, days[0])], data, calendar[:4], 2, direction="short", execution_boundary="open"
    )
    assert [point["nav"] for point in result.series] == pytest.approx([100, 95, 90.5, 86])


def test_short_basket_liquidation_is_permanent():
    days, data, calendar = market()
    data["AAPL"][1]["open"] = 250
    result = construct_policy([signal(1, days[0])], data, calendar, 1, direction="short")
    assert result.liquidated_at == calendar[2]
    assert all(point["nav"] == 0 for point in result.series[1:])
    assert result.active_cohorts == []


def test_missing_open_is_invalid_direct_evidence_and_unavailable_policy():
    days, data, calendar = market()
    del data["AAPL"][1]["open"]
    signals = [signal(1, days[0])]
    result = signal_horizon_statistics(signals, data, calendar, 0.5)
    assert result["invalid_count"] == 1
    assert result["eligible"] is False
    with pytest.raises(ValuationError, match="Missing open price"):
        construct_policy(signals, data, calendar, 0.5)


def test_grid_has_exactly_forty_horizons_and_search_correction():
    days, data, calendar = market(45)
    signals = [signal(index, day) for index, day in enumerate(days[:10])]
    statistics, policies, selected = evaluate_policy_grid(signals, data, calendar)
    assert len(statistics) == len(policies) == len(HORIZONS) == 40
    assert [policy.horizon for policy in policies] == list(HORIZONS)
    assert selected.horizon == 0.5
    assert all(policy.metrics["family_size"] == 40 for policy in policies)
    assert policies[2].metrics["hac_lag"] == 1


def test_pending_horizons_remain_unranked():
    days, data, calendar = market(2)
    statistics, policies, selected = evaluate_policy_grid([signal(1, days[-1])], data, calendar)
    assert selected is None
    assert all(policy.metrics["ci_lower"] is None for policy in policies)
    assert all(item["open_count"] == 1 for item in statistics)


def test_hac_family_adjustment_widens_intervals():
    values = [0.01, -0.01, 0.03, 0.02, 0.04]
    ordinary = hac_mean_statistics(values, lag=1)
    adjusted = hac_mean_statistics(values, lag=1, family_size=40)
    assert adjusted["ci_lower"] < ordinary["ci_lower"]
    assert adjusted["ci_upper"] > ordinary["ci_upper"]


def test_selection_uses_lower_bound_then_shorter_horizon():
    def candidate(horizon, score):
        return PolicyResult(horizon, [], [], [], [], [], 0, metrics={"eligible": True, "ci_lower": score})

    assert select_policy([candidate(5, 0.1), candidate(1, 0.1), candidate(0.5, 0)]).horizon == 1


def test_active_cohort_publishes_scheduled_future_expiry_across_weekend():
    days, data, calendar = market()
    friday = days[4]
    result = construct_policy([signal(1, friday)], data, calendar[:10], 0.5)
    assert result.active_cohorts[0]["end_at"] == calendar[10]
    assert result.active_cohorts[0]["end_at"]["phase"] == "open"
    assert result.active_cohorts[0]["end_at"]["timestamp"][:10] == days[5]


def test_long_spy_signal_and_reference_sleeve_do_not_create_turnover():
    days, data, calendar = market()
    result = construct_policy([signal(1, days[0], "SPY")], data, calendar, 0.5)
    assert result.cumulative_turnover_pct == 0
    assert all(point["nav"] == 100 for point in result.series)


def test_initial_rebuilt_trade_measures_rotation_from_reference():
    days, data, calendar = market()
    result = construct_policy([signal(1, days[0])], data, calendar, 1)
    # One full rotation into AAPL and one full rotation back into SPY.
    assert result.cumulative_turnover_pct == pytest.approx(200)
