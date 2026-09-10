"""Arena orchestration tests."""

from datetime import date

from app.models import Portfolio
from app.services.arena import PortfolioValuation, age_days
from app.services.valuation import ValuationResult


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
