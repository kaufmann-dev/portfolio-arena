"""Arena orchestration tests."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import Allocation, ArenaVersion, Portfolio, Position
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
