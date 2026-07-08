"""US (NYSE) trading-day arithmetic for the no-lookahead rule.

An allocation entered at time T takes effect at the first market close
strictly after T. This module predicts scheduled trading days and close
times (including early closes); the valuation engine itself uses SPY's
*actual* close calendar, so an unscheduled closure merely shifts the
effective close to the next actual close.
"""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(holiday: date) -> date | None:
    """NYSE observance: Saturday -> preceding Friday, Sunday -> following Monday."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def market_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    return {d for d in holidays if d is not None and d.year == year}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def close_time(d: date) -> time:
    """Scheduled close, honoring the 13:00 early closes so an entry between an
    early close and 16:00 cannot claim a close that already happened."""
    july4 = _observed(date(d.year, 7, 4))
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    christmas = _observed(date(d.year, 12, 25))
    early = {
        date(d.year, 7, 3),
        thanksgiving + timedelta(days=1),
        date(d.year, 12, 24),
    }
    # July 3 / Dec 24 are only sessions (and early closes) when the observed
    # holiday itself doesn't consume them and they fall on a weekday.
    if d in early and is_trading_day(d) and d not in (july4, christmas):
        return EARLY_CLOSE
    return REGULAR_CLOSE


def close_at(d: date) -> datetime:
    """The close of trading day d as an aware UTC datetime."""
    return datetime.combine(d, close_time(d), tzinfo=NY).astimezone(UTC)


def effective_date_for(entered_at: datetime) -> date:
    """First trading day whose close is strictly after entered_at."""
    if entered_at.tzinfo is None:
        entered_at = entered_at.replace(tzinfo=UTC)
    d = entered_at.astimezone(NY).date()
    while not (is_trading_day(d) and close_at(d) > entered_at):
        d += timedelta(days=1)
    return d


def is_locked(effective_date: date, now: datetime) -> bool:
    """An allocation locks the moment its effective close has occurred."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now >= close_at(effective_date)
