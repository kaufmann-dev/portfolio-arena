"""Test helpers."""

from datetime import UTC, date, datetime, timedelta

from app.services.trading_calendar import is_trading_day


def past_trading_day(days_back: int):
    """A trading day at least `days_back` calendar days in the past."""
    day = datetime.now(UTC).date() - timedelta(days=days_back)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def set_allocation_effective_date(allocation_id: int, effective_date: date):
    """Set a test allocation to one deterministic trading date and refresh prices."""
    from app.db import session_factory
    from app.models import Allocation

    if not is_trading_day(effective_date):
        raise ValueError("Test allocation effective date must be a trading day")
    with session_factory()() as session:
        allocation = session.get(Allocation, allocation_id)
        allocation.effective_date = effective_date
        allocation.entered_at = datetime.combine(
            effective_date - timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )
        session.commit()
    from app.services.market_refresh import refresh_market_data_once

    refresh_market_data_once()
    return effective_date


def backdate_allocation(allocation_id: int, days_back: int = 30):
    """Move an allocation's effective date into the past so it is locked and
    valued (test-only shortcut around the no-backdating rule)."""
    return set_allocation_effective_date(allocation_id, past_trading_day(days_back))
