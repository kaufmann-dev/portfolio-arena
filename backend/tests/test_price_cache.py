"""Massive cache refresh rules and last-known-data fallback."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import session_factory
from app.models import PriceCache
from app.services import arena, massive, price_cache
from app.services.trading_calendar import is_trading_day

NOW = datetime(2026, 7, 29, 21, 0, tzinfo=UTC)
REQUIRED_START = date(2026, 7, 1)
LATEST = date(2026, 7, 29)


def _series(
    end: date = LATEST,
    *,
    start: date = REQUIRED_START,
    base_close: float = 100.0,
) -> massive.Series:
    points = []
    day = start
    while day <= end:
        if is_trading_day(day):
            points.append(
                {
                    "date": day.isoformat(),
                    "close": base_close + len(points),
                }
            )
        day += timedelta(days=1)
    return points


OLD_SERIES = _series()
NEW_SERIES = _series(base_close=101.0)
LAGGING_SERIES = _series(LATEST - timedelta(days=1), base_close=99.0)


def _required_starts(*symbols: str) -> dict[str, date]:
    return {symbol: REQUIRED_START for symbol in symbols}


def _seed(symbol: str, *, fetched_at: datetime, series=None) -> None:
    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {symbol: series if series is not None else OLD_SERIES},
            fetched_at=fetched_at,
        )


def test_fresh_cache_hit_does_not_fetch(monkeypatch):
    _seed("SPY", fetched_at=NOW - timedelta(minutes=10))
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected refresh")),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=NOW)

    assert loaded.status == "fresh"
    assert loaded.series["SPY"] == OLD_SERIES


@pytest.mark.parametrize(
    ("now", "expected_end"),
    [
        (
            datetime(2026, 7, 29, 20, 14, 59, tzinfo=UTC),
            date(2026, 7, 28),
        ),
        (
            datetime(2026, 7, 29, 20, 15, tzinfo=UTC),
            date(2026, 7, 29),
        ),
        (
            datetime(2026, 7, 30, 0, 30, tzinfo=UTC),
            date(2026, 7, 29),
        ),
    ],
)
def test_refresh_end_is_latest_delayed_eastern_session(monkeypatch, now, expected_end):
    requested_ends = []

    def download(symbols, _start, end):
        requested_ends.append(end)
        return massive.PriceDownloadResult({symbol: _series(end, base_close=101.0) for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)

    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=now)

    assert requested_ends == [expected_end]
    assert loaded.status == "fresh"
    assert loaded.series["SPY"][-1]["date"] == expected_end.isoformat()


def test_newly_available_post_close_session_forces_refresh(monkeypatch):
    _seed("SPY", fetched_at=NOW - timedelta(minutes=10), series=LAGGING_SERIES)
    calls = 0

    def download(symbols, _start, _end):
        nonlocal calls
        calls += 1
        return massive.PriceDownloadResult({symbol: NEW_SERIES for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)
    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=NOW)

    assert calls == 1
    assert loaded.status == "fresh"
    assert loaded.series["SPY"] == NEW_SERIES


def test_successful_refresh_replaces_expired_row(monkeypatch):
    _seed("SPY", fetched_at=NOW - timedelta(hours=2))
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: NEW_SERIES for symbol in symbols}),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert loaded.status == "fresh"
    assert row.series == NEW_SERIES
    assert row.fetched_at == NOW


def test_lagging_refresh_preserves_better_cache_and_enters_cooldown(monkeypatch):
    fetched_at = NOW - timedelta(hours=2)
    _seed("SPY", fetched_at=fetched_at)
    calls = 0

    def download(symbols, _start, _end):
        nonlocal calls
        calls += 1
        return massive.PriceDownloadResult({symbol: LAGGING_SERIES for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)

    with session_factory()() as session:
        first = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
        second = arena.load_price_series(
            session,
            _required_starts("SPY"),
            now=NOW + timedelta(seconds=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert first.status == second.status == "stale"
    assert first.stale_symbols == second.stale_symbols == {"SPY"}
    assert first.series["SPY"] == second.series["SPY"] == OLD_SERIES
    assert row.series == OLD_SERIES
    assert row.fetched_at == fetched_at
    assert calls == 1


def test_no_cache_lagging_series_is_retained_and_reused_during_cooldown(monkeypatch):
    calls = 0

    def download(symbols, _start, _end):
        nonlocal calls
        calls += 1
        return massive.PriceDownloadResult({symbol: LAGGING_SERIES for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)

    with session_factory()() as session:
        first = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
        second = arena.load_price_series(
            session,
            _required_starts("SPY"),
            now=NOW + timedelta(seconds=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert first.status == second.status == "stale"
    assert first.series["SPY"] == second.series["SPY"] == LAGGING_SERIES
    assert row.series == LAGGING_SERIES
    assert row.fetched_at == NOW
    assert calls == 1


def test_broader_lagging_refresh_does_not_regress_narrower_current_cache(monkeypatch):
    narrow_start = REQUIRED_START + timedelta(days=14)
    current_narrow = _series(start=narrow_start, base_close=103.0)
    broader_lagging = _series(end=LATEST - timedelta(days=1), start=REQUIRED_START)
    expected_merged = price_cache.merge_series(broader_lagging, current_narrow)
    _seed("SPY", fetched_at=NOW, series=current_narrow)
    calls = 0

    def download(symbols, _start, _end):
        nonlocal calls
        calls += 1
        return massive.PriceDownloadResult({symbol: broader_lagging for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)

    with session_factory()() as session:
        first = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
        second = arena.load_price_series(
            session,
            _required_starts("SPY"),
            now=NOW + timedelta(seconds=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert first.status == second.status == "stale"
    assert first.series["SPY"] == second.series["SPY"] == expected_merged
    assert row.series == expected_merged
    assert row.start_date == REQUIRED_START
    assert row.end_date == LATEST
    assert row.fetched_at == price_cache.expired_fetched_at(NOW)
    assert calls == 1


def test_current_but_historically_partial_series_is_unavailable(monkeypatch):
    partial = _series(start=date(2026, 7, 15), base_close=101.0)
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: partial for symbol in symbols}),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=NOW)

    assert loaded.status == "unavailable"
    assert loaded.unavailable_symbols == {"SPY"}


def test_transient_failure_uses_stale_row_without_deleting_it(monkeypatch):
    _seed("SPY", fetched_at=NOW - timedelta(hours=2))
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert loaded.status == "stale"
    assert loaded.series["SPY"] == OLD_SERIES
    assert row.series == OLD_SERIES
    assert row.fetched_at == NOW - timedelta(hours=2)


def test_missing_or_incomplete_cache_is_unavailable_after_failure(monkeypatch):
    partial = _series(start=REQUIRED_START + timedelta(days=1))
    _seed(
        "AAPL",
        fetched_at=NOW - timedelta(hours=2),
        series=partial,
    )
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _required_starts("SPY", "AAPL"),
            now=NOW,
        )

    assert loaded.status == "unavailable"
    assert loaded.unavailable_symbols == {"SPY", "AAPL"}
    assert "SPY" not in loaded.series
    assert loaded.series["AAPL"] == partial


def test_failure_cooldown_skips_immediate_retry(monkeypatch):
    calls = 0

    def download(symbols, _start, _end):
        nonlocal calls
        calls += 1
        return massive.PriceDownloadResult({symbol: None for symbol in symbols})

    monkeypatch.setattr(massive, "download_prices", download)
    with session_factory()() as session:
        first = arena.load_price_series(session, _required_starts("SPY"), now=NOW)
        second = arena.load_price_series(
            session,
            _required_starts("SPY"),
            now=NOW + timedelta(seconds=1),
        )

    assert first.status == second.status == "unavailable"
    assert calls == 1


def test_older_fetch_cannot_overwrite_newer_cache_row():
    newer_fetched_at = NOW
    _seed("SPY", fetched_at=newer_fetched_at, series=NEW_SERIES)

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": OLD_SERIES},
            fetched_at=NOW - timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert row.series == NEW_SERIES
    assert row.fetched_at == newer_fetched_at


def test_older_complete_fetch_replaces_newer_lagging_row():
    _seed("SPY", fetched_at=NOW, series=LAGGING_SERIES)

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": OLD_SERIES},
            fetched_at=NOW - timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert row.series == OLD_SERIES
    assert row.end_date == LATEST
    assert row.fetched_at == NOW - timedelta(minutes=1)


def test_broader_lagging_fetch_cannot_replace_narrower_current_row():
    narrow_start = REQUIRED_START + timedelta(days=14)
    current_narrow = _series(start=narrow_start, base_close=103.0)
    _seed("SPY", fetched_at=NOW, series=current_narrow)
    broader_lagging = _series(end=LATEST - timedelta(days=1), start=REQUIRED_START)

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": broader_lagging},
            fetched_at=NOW + timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert row.series == current_narrow
    assert row.start_date == narrow_start
    assert row.end_date == LATEST
    assert row.fetched_at == NOW


def test_newer_series_with_later_actual_start_cannot_discard_history():
    _seed("SPY", fetched_at=NOW, series=OLD_SERIES)
    narrower_newer = _series(
        start=REQUIRED_START + timedelta(days=14),
        end=LATEST + timedelta(days=1),
        base_close=104.0,
    )

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": narrower_newer},
            fetched_at=NOW + timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert row.series == OLD_SERIES
    assert row.start_date == REQUIRED_START
    assert row.end_date == LATEST
    assert row.fetched_at == NOW


def test_lagging_insert_cannot_replace_concurrent_complete_row():
    _seed("SPY", fetched_at=NOW, series=OLD_SERIES)

    with session_factory()() as session:
        price_cache.insert_cached_series_if_missing(
            session,
            {"SPY": LAGGING_SERIES},
            fetched_at=NOW + timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.scalar(select(PriceCache).where(PriceCache.symbol == "SPY"))

    assert row.series == OLD_SERIES
    assert row.end_date == LATEST
    assert row.fetched_at == NOW
