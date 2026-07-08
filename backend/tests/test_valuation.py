"""Valuation engine unit tests with synthetic price/FX fixtures.

The engine is pure: same inputs must always produce identical output.
NAV series are base-100 at the first effective close.
"""
import pytest

from app.services.valuation import (
    AllocationInput,
    PositionInput,
    ValuationError,
    build_calendar,
    compute_metrics,
    rebase_series,
    value_portfolio,
)


def series(*points):
    return [{"date": date, "close": close} for date, close in points]


def equity(symbol, weight):
    return PositionInput(symbol=symbol, instrument="equity", weight_pct=weight)


def cash(currency, weight):
    return PositionInput(symbol=f"CASH:{currency}", instrument="cash", weight_pct=weight)


DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]

SPY = series(*((day, 100.0 + i) for i, day in enumerate(DAYS)))


def calendar(as_of=DAYS[-1]):
    return build_calendar(SPY, as_of)


def test_initial_allocation_deducts_cost_first_then_buys():
    prices = {"AAPL": series((DAYS[0], 50.0), (DAYS[1], 50.0))}
    allocations = [AllocationInput(DAYS[0], (equity("AAPL", 100.0),))]

    result = value_portfolio(allocations, cost_bps=10, prices=prices, calendar=calendar(), as_of=DAYS[1])

    # cost = 100 * 100%/100 * 10/10000 = 0.1, then 99.9 buys shares
    assert result.allocations[0].cost == pytest.approx(0.1)
    assert result.allocations[0].nav_after == pytest.approx(99.9)
    assert result.series[0]["nav"] == pytest.approx(99.9)
    assert result.cumulative_cost == pytest.approx(0.1)


def test_initial_cost_applies_only_to_non_cash_weight():
    prices = {"AAPL": series((DAYS[0], 50.0))}
    allocations = [AllocationInput(DAYS[0], (equity("AAPL", 40.0), cash("USD", 60.0)))]

    result = value_portfolio(allocations, cost_bps=10, prices=prices, calendar=calendar(), as_of=DAYS[0])

    assert result.allocations[0].cost == pytest.approx(100 * 0.4 * 10 / 10_000)


def test_all_cash_portfolio_is_flat_and_free():
    allocations = [AllocationInput(DAYS[0], (cash("USD", 100.0),))]

    result = value_portfolio(allocations, cost_bps=10, prices={}, calendar=calendar(), as_of=DAYS[-1])

    assert result.allocations[0].cost == 0.0
    assert [point["nav"] for point in result.series] == pytest.approx([100.0] * len(DAYS))


def test_nav_drifts_with_prices():
    prices = {
        "AAA": series((DAYS[0], 10.0), (DAYS[1], 11.0), (DAYS[2], 12.0)),
        "BBB": series((DAYS[0], 20.0), (DAYS[1], 20.0), (DAYS[2], 18.0)),
    }
    allocations = [AllocationInput(DAYS[0], (equity("AAA", 50.0), equity("BBB", 50.0)))]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[2])

    # 5 shares AAA, 2.5 shares BBB
    assert result.series[0]["nav"] == pytest.approx(100.0)
    assert result.series[1]["nav"] == pytest.approx(5 * 11.0 + 2.5 * 20.0)
    assert result.series[2]["nav"] == pytest.approx(5 * 12.0 + 2.5 * 18.0)

    # drifted holdings on the last day
    weights = {holding.symbol: holding.weight_pct for holding in result.holdings}
    assert weights["AAA"] == pytest.approx(60.0 / 105.0 * 100.0)
    assert weights["BBB"] == pytest.approx(45.0 / 105.0 * 100.0)


def test_rebalance_turnover_and_two_sided_cost():
    # Constant prices: drifted weights equal targets, so turnover is exactly
    # the target change: 60/40 -> 40/60 = 20% one-sided.
    prices = {
        "AAA": series(*((day, 10.0) for day in DAYS)),
        "BBB": series(*((day, 20.0) for day in DAYS)),
    }
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 60.0), equity("BBB", 40.0))),
        AllocationInput(DAYS[2], (equity("AAA", 40.0), equity("BBB", 60.0))),
    ]

    result = value_portfolio(allocations, cost_bps=10, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    rebalance = result.allocations[1]
    assert rebalance.turnover_pct == pytest.approx(20.0)
    nav_before = rebalance.nav_before
    assert rebalance.cost == pytest.approx(nav_before * 2 * 0.20 * 10 / 10_000)
    assert rebalance.nav_after == pytest.approx(nav_before - rebalance.cost)
    assert result.cumulative_turnover_pct == pytest.approx(20.0)


def test_rebalance_uses_drifted_weights_not_targets():
    # AAA doubles by the rebalance: drifted 2/3 vs target 50 -> selling back to
    # 50/50 turns over |50 - 66.67|/... one-sided = 16.67%.
    prices = {
        "AAA": series((DAYS[0], 10.0), (DAYS[1], 20.0), (DAYS[2], 20.0)),
        "BBB": series((DAYS[0], 10.0), (DAYS[1], 10.0), (DAYS[2], 10.0)),
    }
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 50.0), equity("BBB", 50.0))),
        AllocationInput(DAYS[1], (equity("AAA", 50.0), equity("BBB", 50.0))),
    ]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[2])

    assert result.allocations[1].turnover_pct == pytest.approx(100.0 / 6.0, rel=1e-6)


def test_exit_position_counts_as_turnover():
    prices = {
        "AAA": series(*((day, 10.0) for day in DAYS)),
        "BBB": series(*((day, 20.0) for day in DAYS)),
    }
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 100.0),)),
        AllocationInput(DAYS[2], (equity("BBB", 100.0),)),
    ]

    result = value_portfolio(allocations, cost_bps=10, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    # Sell 100% AAA, buy 100% BBB: one-sided turnover 100%.
    assert result.allocations[1].turnover_pct == pytest.approx(100.0)


def test_cash_to_equity_rebalance_is_free():
    # Turnover is measured over non-cash positions only; equity legs count,
    # cash legs don't add extra.
    prices = {"AAA": series(*((day, 10.0) for day in DAYS))}
    allocations = [
        AllocationInput(DAYS[0], (cash("USD", 100.0),)),
        AllocationInput(DAYS[2], (equity("AAA", 50.0), cash("USD", 50.0))),
    ]

    result = value_portfolio(allocations, cost_bps=10, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    assert result.allocations[1].turnover_pct == pytest.approx(25.0)


def test_multi_currency_cash_floats_with_fx():
    fx = series((DAYS[0], 1.10), (DAYS[1], 1.21), (DAYS[2], 1.10))
    allocations = [AllocationInput(DAYS[0], (cash("EUR", 100.0),))]

    result = value_portfolio(
        allocations, cost_bps=10, prices={"EURUSD=X": fx}, calendar=calendar(), as_of=DAYS[2]
    )

    # No cost on cash; 100 USD -> 90.909 EUR at 1.10
    assert result.series[0]["nav"] == pytest.approx(100.0)
    assert result.series[1]["nav"] == pytest.approx(110.0)  # EUR appreciated 10%
    assert result.series[2]["nav"] == pytest.approx(100.0)


def test_mixed_equity_and_foreign_cash():
    prices = {
        "AAA": series((DAYS[0], 10.0), (DAYS[1], 10.0)),
        "EURUSD=X": series((DAYS[0], 1.00), (DAYS[1], 1.05)),
    }
    allocations = [AllocationInput(DAYS[0], (equity("AAA", 50.0), cash("EUR", 50.0)))]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[1])

    assert result.series[1]["nav"] == pytest.approx(50.0 + 50.0 * 1.05)


def test_effective_date_shifts_to_next_calendar_close():
    # Effective date falls on an unscheduled closure (not in the calendar):
    # the allocation applies at the next actual close.
    prices = {"AAA": series(*((day, 10.0) for day in DAYS))}
    allocations = [AllocationInput("2026-01-04", (equity("AAA", 100.0),))]  # Sunday

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    assert result.allocations[0].applied_date == DAYS[0]
    assert result.series[0]["date"] == DAYS[0]


def test_future_allocation_is_pending():
    prices = {"AAA": series(*((day, 10.0) for day in DAYS))}
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 100.0),)),
        AllocationInput("2026-02-01", (cash("USD", 100.0),)),
    ]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    pending = [a for a in result.allocations if a.applied_date is None]
    assert len(pending) == 1
    assert pending[0].effective_date == "2026-02-01"
    # NAV series unaffected by the pending allocation
    assert result.series[-1]["nav"] == pytest.approx(100.0)


def test_missing_price_carries_forward_and_flags_stale():
    prices = {
        "AAA": series((DAYS[0], 10.0), (DAYS[1], 12.0), (DAYS[3], 14.0)),  # DAYS[2] missing
    }
    allocations = [AllocationInput(DAYS[0], (equity("AAA", 100.0),))]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[3])

    assert result.series[2]["nav"] == pytest.approx(result.series[1]["nav"])  # carried
    assert result.stale_days == {"AAA": [DAYS[2]]}
    assert result.frozen_symbols == []


def test_delisted_symbol_freezes_and_warns():
    # AAA stops printing after day 0; 5+ trading days of silence => frozen.
    days = [f"2026-02-{str(dom).zfill(2)}" for dom in (2, 3, 4, 5, 6, 9, 10)]
    spy = series(*((day, 100.0) for day in days))
    prices = {"AAA": series((days[0], 10.0))}
    allocations = [AllocationInput(days[0], (equity("AAA", 100.0),))]

    result = value_portfolio(
        allocations, cost_bps=0, prices=prices, calendar=build_calendar(spy, days[-1]), as_of=days[-1]
    )

    assert result.frozen_symbols == ["AAA"]
    assert result.series[-1]["nav"] == pytest.approx(100.0)  # frozen at last price
    assert result.stale_days["AAA"] == days[1:]


def test_no_price_at_or_before_first_close_raises():
    prices = {"AAA": series((DAYS[2], 10.0))}
    allocations = [AllocationInput(DAYS[0], (equity("AAA", 100.0),))]

    with pytest.raises(ValuationError):
        value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])


def test_zero_weight_positions_are_ignored():
    prices = {"AAA": series(*((day, 10.0) for day in DAYS))}
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 100.0), equity("MISSING-DATA", 0.0)))
    ]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    assert len(result.holdings) == 1


def test_determinism():
    prices = {
        "AAA": series(*((day, 10.0 + i) for i, day in enumerate(DAYS))),
        "EURUSD=X": series(*((day, 1.1 + i / 100) for i, day in enumerate(DAYS))),
    }
    allocations = [
        AllocationInput(DAYS[0], (equity("AAA", 70.0), cash("EUR", 30.0))),
        AllocationInput(DAYS[2], (equity("AAA", 40.0), cash("EUR", 60.0))),
    ]

    first = value_portfolio(allocations, cost_bps=25, prices=prices, calendar=calendar(), as_of=DAYS[-1])
    second = value_portfolio(allocations, cost_bps=25, prices=prices, calendar=calendar(), as_of=DAYS[-1])

    assert first.series == second.series
    assert first.allocations == second.allocations
    assert first.holdings == second.holdings


def test_metrics_vs_spy_identical_window():
    prices = {"AAA": series(*((day, 10.0) for day in DAYS))}  # flat portfolio
    allocations = [AllocationInput(DAYS[1], (equity("AAA", 100.0),))]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])
    metrics = compute_metrics(result, SPY)

    # Window starts at DAYS[1] (SPY=101), ends DAYS[4] (SPY=104)
    assert metrics["itd_return"] == pytest.approx(0.0)
    assert metrics["spy_return"] == pytest.approx(104.0 / 101.0 - 1.0)
    assert metrics["vs_spy"] == pytest.approx(-(104.0 / 101.0 - 1.0))


def test_metrics_max_drawdown_and_shape():
    navs = [100.0, 110.0, 99.0, 104.5, 121.0]
    prices = {"AAA": series(*((day, nav) for day, nav in zip(DAYS, navs, strict=True)))}
    allocations = [AllocationInput(DAYS[0], (equity("AAA", 100.0),))]

    result = value_portfolio(allocations, cost_bps=0, prices=prices, calendar=calendar(), as_of=DAYS[-1])
    metrics = compute_metrics(result, SPY)

    assert metrics["max_drawdown"] == pytest.approx(99.0 / 110.0 - 1.0)
    assert metrics["itd_return"] == pytest.approx(0.21)
    assert metrics["ann_volatility"] > 0
    assert metrics["r1m"] is None  # too young for trailing windows


def test_metrics_empty_series():
    result = value_portfolio([], cost_bps=0, prices={}, calendar=calendar(), as_of=DAYS[-1])
    assert compute_metrics(result, SPY) == {"has_data": False}


def test_rebase_series_windows():
    rebased = rebase_series(SPY, DAYS[1], DAYS[3])
    assert [point["date"] for point in rebased] == DAYS[1:4]
    assert rebased[0]["nav"] == pytest.approx(100.0)
    assert rebased[-1]["nav"] == pytest.approx(103.0 / 101.0 * 100.0)
