"""Arena orchestration tests."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import Allocation, ArenaVersion, Portfolio, Position, Signal, SignalPosition
from app.services import arena
from app.services.arena import PortfolioValuation, age_days, managed_valuation_boundary
from app.services.trading_calendar import boundary_value, is_trading_day
from app.services.valuation import ValuationResult, compute_metrics, point_boundary


def test_age_uses_current_calendar_date_instead_of_last_valued_close():
    valuation = PortfolioValuation(
        portfolio=Portfolio(),
        result=ValuationResult(
            series=[{"timestamp": "2026-07-27T20:00:00+00:00", "phase": "close", "nav": 100.0}],
            allocations=[],
            holdings=[],
        ),
        metrics={},
    )

    assert age_days(valuation, date(2026, 7, 29)) == 2


def test_morning_readiness_uses_old_close_holdings_until_new_decision_executes():
    from types import SimpleNamespace

    from app.services.arena import managed_readiness_symbols

    def allocation(day, identifier, symbol):
        return SimpleNamespace(effective_date=day, id=identifier, positions=[SimpleNamespace(symbol=symbol)])

    portfolio = SimpleNamespace(
        execution_boundary="close",
        allocations=[allocation(date(2026, 7, 27), 1, "AAPL"), allocation(date(2026, 7, 28), 2, "MSFT")],
    )
    assert managed_readiness_symbols([portfolio], date(2026, 7, 28), "open") == {"SPY", "AAPL"}
    assert managed_readiness_symbols([portfolio], date(2026, 7, 28), "close") == {"SPY", "MSFT"}


@pytest.mark.parametrize("phase", ["open", "close"])
def test_paused_cutoff_skips_holidays_and_ignores_pending_decisions(phase):
    portfolio = Portfolio(
        version=ArenaVersion(evaluation_enabled=False),
        execution_boundary=phase,
        allocations=[
            Allocation(effective_date=date(2026, 7, 2)),
            Allocation(effective_date=date(2026, 7, 20)),
        ],
    )
    # July 3 is a market holiday; the next session is July 6.
    cutoff = boundary_value(date(2026, 7, 6), phase)
    assert managed_valuation_boundary(portfolio, boundary_value(date(2026, 7, 17), "close")) == cutoff
    before_cutoff = boundary_value(date(2026, 7, 2), "close")
    assert managed_valuation_boundary(portfolio, before_cutoff) == before_cutoff
    portfolio.allocations = []
    assert managed_valuation_boundary(portfolio, before_cutoff) == before_cutoff


@pytest.mark.parametrize("phase", ["open", "close"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_version_pause_caps_valuation_and_resume_restores_all_returns(monkeypatch, phase, direction):
    portfolio = Portfolio(
        id=101,
        version_id=101,
        version=ArenaVersion(evaluation_enabled=True),
        prompt_mode="managed",
        direction=direction,
        execution_boundary=phase,
        allocations=[
            Allocation(
                id=101,
                effective_date=date(2026, 7, 2),
                positions=[Position(symbol="AAPL", weight_pct=100, note="Hold")],
            )
        ],
    )
    days = [date(2026, 7, 2) + timedelta(days=i) for i in range(16)]
    prices = {
        symbol: [
            {"date": day.isoformat(), "open": 100 + i * rate, "close": 100.5 + i * rate}
            for i, day in enumerate(days)
            if is_trading_day(day)
        ]
        for symbol, rate in [("SPY", 0.2), ("AAPL", 0.5)]
    }
    latest = boundary_value(date(2026, 7, 17), "close")
    monkeypatch.setattr(
        arena, "load_price_series", lambda *args: arena.PriceSeriesLoad(prices, "fresh", latest, latest)
    )

    def compute():
        return arena.compute_valuations(None, [portfolio], datetime(2026, 7, 18, tzinfo=UTC))

    enabled = compute().by_portfolio_id[portfolio.id]
    assert point_boundary(enabled.result.series[-1]) == latest
    portfolio.version.evaluation_enabled = False
    paused_arena = compute()
    paused = paused_arena.by_portfolio_id[portfolio.id]
    cutoff = boundary_value(date(2026, 7, 6), phase)
    assert point_boundary(paused.result.series[-1]) == cutoff
    assert paused.result.series == [
        point for point in enabled.result.series if point["timestamp"] <= cutoff["timestamp"]
    ]
    truncated_spy = [point for point in prices["SPY"] if point["date"] <= "2026-07-06"]
    assert paused.metrics == compute_metrics(paused.result, truncated_spy, direction)
    portfolio.version.evaluation_enabled = True
    resumed = compute().by_portfolio_id[portfolio.id]
    assert resumed.result == enabled.result
    assert resumed.metrics == enabled.metrics


def test_latest_effective_abstention_renews_paused_cutoff():
    portfolio = Portfolio(
        version=ArenaVersion(evaluation_enabled=False),
        execution_boundary="close",
        allocations=[
            Allocation(effective_date=date(2026, 7, 2)),
            Allocation(effective_date=date(2026, 7, 9), positions=[]),
        ],
    )
    assert managed_valuation_boundary(
        portfolio, boundary_value(date(2026, 7, 17), "close")
    ) == boundary_value(date(2026, 7, 10), "close")


@pytest.mark.parametrize("direction", ["long", "short"])
def test_rebuilt_missing_ticker_does_not_block_other_portfolios_and_repairs_cache(monkeypatch, direction):
    from app.services import price_cache

    days = [date(2026, 7, day) for day in (20, 21, 22, 23, 24)]
    prices = {
        symbol: [
            {"date": day.isoformat(), "open": 100 + index * rate, "close": 100 + index * rate}
            for index, day in enumerate(days)
        ]
        for symbol, rate in (("SPY", 1), ("AAPL", -5), ("MSFT", 2))
    }
    complete_aapl = prices["AAPL"]
    prices["AAPL"] = complete_aapl[:2]
    now = datetime(2026, 7, 25, tzinfo=UTC)
    monkeypatch.setattr(
        price_cache,
        "get_cache_entries",
        lambda _session, symbols: {
            symbol: price_cache.cache_entry(prices[symbol], now) for symbol in symbols
        },
    )
    portfolios = [
        Portfolio(
            id=identifier,
            version_id=1,
            prompt_mode="rebuilt",
            direction=direction,
            execution_boundary="close",
            signals=[
                Signal(
                    id=identifier * 10 + index,
                    effective_date=day,
                    positions=[SignalPosition(symbol=symbol, weight_pct=100, note="")],
                )
                for index, day in enumerate(days[:2])
            ],
        )
        for identifier, symbol in ((1, "AAPL"), (2, "MSFT"))
    ]
    arena.clear_analysis_caches()
    result = arena.compute_rebuilt_arena(None, portfolios, now)
    assert result.as_of == boundary_value(days[-1], "close")
    affected, healthy = (result.by_portfolio_id[index] for index in (1, 2))
    assert affected.error is None
    assert affected.frozen_symbols == ["AAPL"]
    assert affected.stale_data is True
    assert healthy.frozen_symbols == []
    assert healthy.stale_data is False
    for analysis in (affected, healthy):
        assert analysis.selected is not None
        assert analysis.selected.series[-1]["timestamp"] == result.as_of["timestamp"]
        assert all(item["invalid_count"] == 0 for item in analysis.signal_horizons)
    # A cached affected result must not contaminate the next portfolio's warning.
    cached = arena.compute_rebuilt_arena(None, portfolios, now)
    assert cached.by_portfolio_id[2].frozen_symbols == []
    prices["AAPL"] = complete_aapl
    repaired = arena.compute_rebuilt_arena(None, portfolios, now).by_portfolio_id[1]
    assert repaired.frozen_symbols == []
    assert repaired.stale_data is False
    assert repaired.policies[2].series != affected.policies[2].series
