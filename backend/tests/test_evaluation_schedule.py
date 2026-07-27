"""Evaluator cadence and configurable NYSE-window selection."""

from datetime import date, time

from app.models import EvaluatorSettings, PortfolioEvaluatorConfig
from app.services.evaluator import is_due_on, scheduled_enqueue_window
from app.services.trading_calendar import NY


def test_early_close_queue_window_uses_configured_offset_and_close():
    settings = EvaluatorSettings(
        id=1,
        queue_before_close_minutes=120,
    )

    queue_at, closes_at = scheduled_enqueue_window(date(2026, 11, 27), settings)

    assert queue_at.astimezone(NY).time() == time(11)
    assert closes_at.astimezone(NY).time() == time(13)


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
