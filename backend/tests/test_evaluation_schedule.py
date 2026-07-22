"""Arena-owned evaluation-window selection."""

from datetime import UTC, date, datetime, time

from app.services.evaluation_schedule import evaluation_window, get_evaluation_schedule
from app.services.trading_calendar import NY


def ny(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NY)


def test_before_open_returns_todays_upcoming_session():
    schedule = get_evaluation_schedule(ny(2026, 7, 20, 12))

    assert schedule["scheduled_for"] == "2026-07-20"
    assert schedule["state"] == "upcoming"
    assert schedule["server_time"] == ny(2026, 7, 20, 12).astimezone(UTC).isoformat()


def test_during_window_returns_todays_open_session():
    schedule = get_evaluation_schedule(ny(2026, 7, 20, 15))

    assert schedule["scheduled_for"] == "2026-07-20"
    assert schedule["state"] == "open"


def test_at_cutoff_returns_next_session():
    schedule = get_evaluation_schedule(ny(2026, 7, 20, 15, 50))

    assert schedule["scheduled_for"] == "2026-07-21"
    assert schedule["state"] == "upcoming"


def test_non_trading_day_returns_next_session():
    schedule = get_evaluation_schedule(ny(2026, 7, 18, 12))

    assert schedule["scheduled_for"] == "2026-07-20"
    assert schedule["state"] == "upcoming"


def test_early_close_window_uses_scheduled_close():
    opens_at, cutoff_at = evaluation_window(date(2026, 11, 27))

    assert opens_at.astimezone(NY).time() == time(11, 30)
    assert cutoff_at.astimezone(NY).time() == time(12, 50)
