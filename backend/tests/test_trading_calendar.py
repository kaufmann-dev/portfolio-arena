"""No-lookahead effective-date rules: weekends, holidays, early closes, locking."""

from datetime import UTC, date, datetime

from app.services.trading_calendar import (
    NY,
    close_at,
    close_time,
    effective_date_for,
    is_locked,
    is_trading_day,
    market_holidays,
)


def ny(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=NY)


class TestHolidays:
    def test_2026_holidays(self):
        holidays = market_holidays(2026)
        assert date(2026, 1, 1) in holidays  # New Year's Day (Thursday)
        assert date(2026, 1, 19) in holidays  # MLK Day
        assert date(2026, 2, 16) in holidays  # Washington's Birthday
        assert date(2026, 4, 3) in holidays  # Good Friday
        assert date(2026, 5, 25) in holidays  # Memorial Day
        assert date(2026, 6, 19) in holidays  # Juneteenth (Friday)
        assert date(2026, 7, 3) in holidays  # July 4 observed (Sat -> Friday)
        assert date(2026, 9, 7) in holidays  # Labor Day
        assert date(2026, 11, 26) in holidays  # Thanksgiving
        assert date(2026, 12, 25) in holidays  # Christmas (Friday)

    def test_sunday_holiday_observed_monday(self):
        # July 4 2027 is a Sunday -> observed Monday July 5
        assert date(2027, 7, 5) in market_holidays(2027)
        assert not is_trading_day(date(2027, 7, 5))

    def test_weekends_are_not_trading_days(self):
        assert not is_trading_day(date(2026, 7, 11))  # Saturday
        assert not is_trading_day(date(2026, 7, 12))  # Sunday
        assert is_trading_day(date(2026, 7, 13))  # Monday


class TestEffectiveDate:
    def test_entry_during_trading_day_effective_same_close(self):
        assert effective_date_for(ny(2026, 7, 8, 10)) == date(2026, 7, 8)  # Wednesday 10:00

    def test_entry_after_close_effective_next_day(self):
        assert effective_date_for(ny(2026, 7, 8, 17)) == date(2026, 7, 9)

    def test_entry_at_exact_close_is_not_same_day(self):
        # Close at exactly 16:00 is not strictly after T.
        assert effective_date_for(ny(2026, 7, 8, 16)) == date(2026, 7, 9)

    def test_saturday_entry_effective_monday(self):
        assert effective_date_for(ny(2026, 7, 11, 12)) == date(2026, 7, 13)

    def test_friday_after_close_skips_weekend(self):
        assert effective_date_for(ny(2026, 7, 10, 20)) == date(2026, 7, 13)

    def test_holiday_entry_skips_to_next_trading_day(self):
        # Friday July 3 2026 is the observed July 4 holiday.
        assert effective_date_for(ny(2026, 7, 3, 10)) == date(2026, 7, 6)

    def test_good_friday(self):
        assert effective_date_for(ny(2026, 4, 2, 18)) == date(2026, 4, 6)  # Thu after close -> Mon

    def test_early_close_day_after_thanksgiving(self):
        # Friday Nov 27 2026 closes 13:00; a 14:00 entry cannot claim it.
        assert close_time(date(2026, 11, 27)).hour == 13
        assert effective_date_for(ny(2026, 11, 27, 14)) == date(2026, 11, 30)
        assert effective_date_for(ny(2026, 11, 27, 12)) == date(2026, 11, 27)

    def test_christmas_eve_early_close(self):
        # Thursday Dec 24 2026 is a 13:00 session (Christmas on Friday).
        assert close_time(date(2026, 12, 24)).hour == 13
        assert effective_date_for(ny(2026, 12, 24, 13, 30)) == date(2026, 12, 28)

    def test_utc_input(self):
        # 2026-07-08 19:00 UTC = 15:00 New York (EDT) -> same-day close.
        assert effective_date_for(datetime(2026, 7, 8, 19, 0, tzinfo=UTC)) == date(2026, 7, 8)


class TestLocking:
    def test_unlocked_before_close(self):
        assert not is_locked(date(2026, 7, 8), ny(2026, 7, 8, 15, 59))

    def test_locked_at_close(self):
        assert is_locked(date(2026, 7, 8), ny(2026, 7, 8, 16))

    def test_locked_after_close(self):
        assert is_locked(date(2026, 7, 8), ny(2026, 7, 9, 9))

    def test_close_at_is_utc(self):
        close = close_at(date(2026, 7, 8))
        assert close.tzinfo is UTC
        assert close == ny(2026, 7, 8, 16)
