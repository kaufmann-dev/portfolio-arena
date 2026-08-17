"""Arena orchestration tests."""

from datetime import date
from types import SimpleNamespace

from app.models import Portfolio
from app.services import arena
from app.services.arena import PortfolioValuation, age_days
from app.services.valuation import ValuationResult


def test_age_uses_current_calendar_date_instead_of_last_valued_close():
    valuation = PortfolioValuation(
        portfolio=Portfolio(),
        result=ValuationResult(
            series=[{"date": "2026-07-27", "nav": 100.0}],
            allocations=[],
            holdings=[],
        ),
        metrics={},
    )

    assert age_days(valuation, date(2026, 7, 29)) == 2


def test_rebuilt_market_flags_ignore_prices_after_a_completed_h20_lifecycle():
    calendar = [f"2026-07-{day:02d}" for day in range(1, 31)]
    portfolio = SimpleNamespace(
        signals=[
            SimpleNamespace(
                effective_date=date(2026, 7, 1),
                positions=[SimpleNamespace(symbol="AAPL", weight_pct=100)],
            )
        ]
    )
    prices = {
        "AAPL": [{"date": day, "close": 100.0} for day in calendar[:21]],
    }

    stale, frozen = arena._rebuilt_market_flags(
        portfolio,
        prices,
        calendar,
        calendar[-1],
    )

    assert stale is False
    assert frozen == []
