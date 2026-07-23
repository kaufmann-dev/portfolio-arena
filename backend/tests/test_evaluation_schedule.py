"""Evaluator cadence and configurable NYSE-window selection."""

from datetime import date, time

from app.models import EvaluatorSettings, PortfolioEvaluatorConfig
from app.services.evaluator import evaluation_window, is_due_on
from app.services.trading_calendar import NY


def test_early_close_window_uses_configured_offsets():
    settings = EvaluatorSettings(
        id=1,
        start_before_close_minutes=120,
        cutoff_before_close_minutes=15,
    )

    opens_at, cutoff_at = evaluation_window(date(2026, 11, 27), settings)

    assert opens_at.astimezone(NY).time() == time(11)
    assert cutoff_at.astimezone(NY).time() == time(12, 45)


def test_selected_holiday_weekday_shifts_to_next_trading_day():
    friday_only = PortfolioEvaluatorConfig(
        portfolio_id=1,
        enabled=True,
        weekdays=[4],
    )

    assert is_due_on(friday_only, date(2026, 7, 6))


def test_shifted_and_regular_cadence_deduplicate_to_one_session():
    friday_and_monday = PortfolioEvaluatorConfig(
        portfolio_id=1,
        enabled=True,
        weekdays=[0, 4],
    )

    assert is_due_on(friday_and_monday, date(2026, 7, 6))


def test_empty_weekday_selection_is_manual_only():
    manual = PortfolioEvaluatorConfig(
        portfolio_id=1,
        enabled=True,
        weekdays=[],
    )

    assert not is_due_on(manual, date(2026, 7, 6))
