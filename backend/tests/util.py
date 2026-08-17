"""Test helpers."""

from datetime import UTC, datetime, timedelta

from app.services.trading_calendar import is_trading_day


def past_trading_day(days_back: int):
    """A trading day at least `days_back` calendar days in the past."""
    day = datetime.now(UTC).date() - timedelta(days=days_back)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def backdate_allocation(allocation_id: int, days_back: int = 30):
    """Move an allocation's effective date into the past so it is locked and
    valued (test-only shortcut around the no-backdating rule)."""
    from app.db import session_factory
    from app.models import Allocation

    day = past_trading_day(days_back)
    with session_factory()() as session:
        allocation = session.get(Allocation, allocation_id)
        allocation.effective_date = day
        allocation.entered_at = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        session.commit()
    from app.services.market_refresh import refresh_market_data_once

    refresh_market_data_once()
    return day
