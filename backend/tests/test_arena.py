"""Arena orchestration tests."""

from datetime import date

from app.models import Portfolio
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
