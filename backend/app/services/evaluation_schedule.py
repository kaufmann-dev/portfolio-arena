"""Server-owned scheduling for automated portfolio evaluations."""

from datetime import UTC, date, datetime, timedelta

from .trading_calendar import NY, close_at, is_trading_day

EVALUATION_START_BEFORE_CLOSE = timedelta(minutes=90)
EVALUATION_CUTOFF_BEFORE_CLOSE = timedelta(minutes=10)


def evaluation_window(scheduled_for: date) -> tuple[datetime, datetime]:
    """Return the open and cutoff instants for one scheduled NYSE session."""
    close = close_at(scheduled_for)
    return (
        close - EVALUATION_START_BEFORE_CLOSE,
        close - EVALUATION_CUTOFF_BEFORE_CLOSE,
    )


def _next_trading_day(day: date) -> date:
    candidate = day
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def get_evaluation_schedule(now: datetime | None = None) -> dict[str, str]:
    """Return the current evaluation window, or the next one after its cutoff."""
    server_time = now or datetime.now(UTC)
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=UTC)
    server_time = server_time.astimezone(UTC)

    local_day = server_time.astimezone(NY).date()
    scheduled_for = _next_trading_day(local_day)
    opens_at, cutoff_at = evaluation_window(scheduled_for)

    if server_time >= cutoff_at:
        scheduled_for = _next_trading_day(scheduled_for + timedelta(days=1))
        opens_at, cutoff_at = evaluation_window(scheduled_for)

    state = "open" if opens_at <= server_time < cutoff_at else "upcoming"
    return {
        "server_time": server_time.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "opens_at": opens_at.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "state": state,
    }
