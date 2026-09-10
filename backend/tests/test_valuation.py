"""Hand-calculated managed portfolios on opening and closing boundaries."""

from datetime import date

import pytest

from app.services.trading_calendar import boundary_value
from app.services.valuation import (
    AllocationInput,
    PositionInput,
    ValuationError,
    build_calendar,
    compute_metrics,
    point_boundary,
    rebase_series,
    value_portfolio,
)

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def boundary(day, phase="close"):
    return boundary_value(date.fromisoformat(day), phase)


def prices(values):
    return [
        {"date": day, "open": opening, "close": close}
        for day, (opening, close) in zip(DAYS, values, strict=True)
    ]


def position(symbol="AAPL", weight=100):
    return PositionInput(symbol, weight)


SPY = prices([(100, 100), (100, 100), (100, 100)])


def value(allocations, data, phase="close", direction="long", as_of=None):
    end = as_of or boundary(DAYS[-1])
    return value_portfolio(allocations, data, build_calendar(SPY, end), end, direction, phase)


def test_close_execution_starts_at_close_and_marks_next_open():
    result = value(
        [AllocationInput(DAYS[0], (position(),))], {"AAPL": prices([(10, 20), (30, 40), (50, 60)])}
    )
    assert [point["nav"] for point in result.series] == pytest.approx([100, 150, 200, 250, 300])
    assert result.series[0]["phase"] == "close"
    assert result.allocations[0].applied_at == boundary(DAYS[0])
    assert result.holdings[0].entry_price == 20


def test_open_execution_uses_open_and_keeps_same_day_close_distinct():
    result = value(
        [AllocationInput(DAYS[0], (position(),))],
        {"AAPL": prices([(10, 20), (30, 40), (50, 60)])},
        phase="open",
    )
    assert [point["nav"] for point in result.series] == pytest.approx([100, 200, 300, 400, 500, 600])
    assert result.series[0]["timestamp"] != result.series[1]["timestamp"]
    assert result.allocations[0].effective_at == boundary(DAYS[0], "open")


def test_marks_do_not_rebalance_drifted_weights():
    data = {"AAPL": prices([(10, 20), (30, 40), (50, 60)]), "MSFT": prices([(10, 10), (10, 10), (10, 10)])}
    allocation = AllocationInput(DAYS[0], (position("AAPL", 50), position("MSFT", 50)))
    result = value([allocation], data, phase="open")
    assert result.series[-1]["nav"] == pytest.approx(350)
    assert result.cumulative_turnover_pct == 0
    assert len(result.allocations) == 1
    assert result.holdings[0].weight_pct == pytest.approx(300 / 350 * 100)


def test_rebalance_has_no_cost_and_retains_turnover():
    data = {
        "AAPL": prices([(100, 100), (120, 120), (120, 120)]),
        "MSFT": prices([(100, 100), (100, 100), (110, 110)]),
    }
    result = value(
        [AllocationInput(DAYS[0], (position(),)), AllocationInput(DAYS[1], (position("MSFT"),))], data
    )
    assert result.series[-1]["nav"] == pytest.approx(132)
    assert result.allocations[1].nav_before == result.allocations[1].nav_after == pytest.approx(120)
    assert result.cumulative_turnover_pct == pytest.approx(100)


def test_future_allocation_is_pending():
    result = value([AllocationInput("2026-01-08", (position(),))], {"AAPL": SPY})
    assert result.series == []
    assert result.allocations[0].applied_at is None


def test_short_shares_are_fixed_between_decisions():
    result = value(
        [AllocationInput(DAYS[0], (position(),))],
        {"AAPL": prices([(100, 100), (110, 120), (110, 100)])},
        direction="short",
    )
    assert [point["nav"] for point in result.series] == pytest.approx([100, 90, 80, 90, 100])


def test_short_liquidates_at_open_and_never_recovers():
    result = value(
        [AllocationInput(DAYS[0], (position(),))],
        {"AAPL": prices([(100, 100), (210, 150), (50, 50)])},
        direction="short",
    )
    assert result.liquidated_at == boundary(DAYS[1], "open")
    assert [point["nav"] for point in result.series] == [100, 0, 0, 0, 0]
    assert result.holdings == []


def test_short_reference_resets_only_at_close():
    spy = prices([(100, 110), (121, 132), (132, 132)])
    result = rebase_series(spy, boundary(DAYS[0], "open"), boundary(DAYS[-1]), "short")
    # Initial day 100->90; next open marks 81 without resetting, close is72.
    assert [point["nav"] for point in result] == pytest.approx([100, 90, 81, 72, 72, 72])


def test_missing_open_is_not_replaced_by_close():
    data = prices([(100, 100), (110, 120), (110, 100)])
    del data[1]["open"]
    with pytest.raises(ValuationError, match="Missing open price"):
        value([AllocationInput(DAYS[0], (position(),))], {"AAPL": data})


def test_calendar_preserves_missing_open_for_horizon_accounting():
    spy = [dict(point) for point in SPY]
    del spy[1]["open"]
    events = build_calendar(spy, boundary(DAYS[-1]))
    assert len(events) == 6
    assert events[2] == boundary(DAYS[1], "open")


def test_metrics_use_full_session_returns_and_252_annualization():
    data = prices([(100, 101), (110, 121), (132, 133)])
    result = value(
        [AllocationInput(DAYS[0], (position(),))],
        {"AAPL": data},
        phase="open",
        as_of=boundary(DAYS[-1], "open"),
    )
    metrics = compute_metrics(result, SPY)
    assert metrics["observation_count"] == 2
    assert metrics["mean_daily_alpha"] == pytest.approx(0.15)
    assert metrics["ann_volatility"] == pytest.approx((0.005**0.5) * (252**0.5))
    assert metrics["itd_return"] == pytest.approx(0.32)
    assert metrics["end_at"] == point_boundary(result.series[-1])


def test_close_statistics_exclude_partial_open_to_close_entry_interval():
    result = value(
        [AllocationInput(DAYS[0], (position(),))],
        {"AAPL": prices([(100, 110), (120, 121), (130, 133.1)])},
        phase="open",
    )
    metrics = compute_metrics(result, SPY)
    assert metrics["observation_count"] == 2
    assert metrics["mean_daily_alpha"] == pytest.approx(0.1)
    assert metrics["itd_return"] == pytest.approx(0.331)


def test_missing_full_spy_session_preserves_calendar_and_fails_pricing():
    spy = [SPY[0], SPY[2]]
    calendar = build_calendar(spy, boundary(DAYS[-1]))
    assert len(calendar) == 6
    assert calendar[2] == boundary(DAYS[1], "open")
    with pytest.raises(ValuationError, match="Missing open price for SPY"):
        rebase_series(spy, boundary(DAYS[0]), boundary(DAYS[-1]))
