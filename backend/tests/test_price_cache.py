"""Cache-only reads and atomic background market-data publication."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db import session_factory
from app.models import PriceCache
from app.services import arena, market_refresh, massive, price_cache
from app.services.trading_calendar import is_trading_day

REQUIRED_START = date(2026, 7, 1)
PRIOR = date(2026, 7, 28)
TARGET = date(2026, 7, 29)
UPDATING_NOW = datetime(2026, 7, 29, 20, 16, tzinfo=UTC)
STALE_NOW = datetime(2026, 7, 29, 20, 26, tzinfo=UTC)


def _series(
    end: date = TARGET,
    *,
    start: date = REQUIRED_START,
    base_close: float = 100.0,
) -> massive.Series:
    points = []
    day = start
    while day <= end:
        if is_trading_day(day):
            points.append({"date": day.isoformat(), "close": base_close + len(points)})
        day += timedelta(days=1)
    return points


OLD_SERIES = _series(PRIOR)
NEW_SERIES = _series(base_close=101.0)


def _requirements(*symbols: str) -> dict[str, date]:
    return {symbol: REQUIRED_START for symbol in symbols}


def _seed(symbol: str, *, fetched_at: datetime = UPDATING_NOW, series=None) -> None:
    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {symbol: series if series is not None else OLD_SERIES},
            fetched_at=fetched_at,
        )


def _stub_requirements(monkeypatch) -> None:
    monkeypatch.setattr(arena, "load_portfolios", lambda _session: [])
    monkeypatch.setattr(
        arena,
        "global_pricing_requirements",
        lambda _portfolios, _target: (
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
        ),
    )


def test_request_read_is_cache_only_during_publication_lag(monkeypatch):
    _seed("SPY")
    _seed("AAPL")
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request performed I/O")),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
            now=UPDATING_NOW,
        )

    assert loaded.status == "updating"
    assert loaded.as_of == PRIOR.isoformat()
    assert loaded.target_as_of == TARGET.isoformat()
    assert loaded.stale_symbols == set()


def test_publication_lag_becomes_stale_only_after_grace_period():
    _seed("SPY")
    _seed("AAPL")

    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
            now=STALE_NOW,
        )

    assert loaded.status == "stale"
    assert loaded.as_of == PRIOR.isoformat()
    assert loaded.stale_symbols == {"SPY", "AAPL"}


def test_missing_history_is_unavailable_without_provider_io(monkeypatch):
    _seed("SPY")
    _seed("AAPL", series=_series(start=date(2026, 7, 15)))
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request performed I/O")),
    )

    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
            now=UPDATING_NOW,
        )

    assert loaded.status == "unavailable"
    assert loaded.unavailable_symbols == {"AAPL"}


def test_background_refresh_withholds_partial_target_batch(monkeypatch):
    _seed("SPY")
    _seed("AAPL")
    _stub_requirements(monkeypatch)
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda _symbols, _start, _end: massive.PriceDownloadResult({"SPY": NEW_SERIES, "AAPL": OLD_SERIES}),
    )

    with session_factory()() as session:
        outcome = market_refresh._refresh_locked(session, UPDATING_NOW, TARGET)
    with session_factory()() as session:
        rows = {row.symbol: row for row in session.scalars(select(PriceCache)).all()}

    assert outcome.complete is False
    assert outcome.updated_symbols == ()
    assert rows["SPY"].series == OLD_SERIES
    assert rows["AAPL"].series == OLD_SERIES


def test_background_refresh_publishes_complete_target_batch(monkeypatch):
    _seed("SPY")
    _seed("AAPL")
    _stub_requirements(monkeypatch)
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult(
            {symbol: _series(base_close=101.0 if symbol == "SPY" else 201.0) for symbol in symbols}
        ),
    )

    with session_factory()() as session:
        outcome = market_refresh._refresh_locked(session, UPDATING_NOW, TARGET)
    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
            now=UPDATING_NOW,
        )

    assert outcome.complete is True
    assert outcome.updated_symbols == ("AAPL", "SPY")
    assert loaded.status == "fresh"
    assert loaded.as_of == TARGET.isoformat()


def test_after_grace_background_publishes_degraded_snapshot(monkeypatch):
    _seed("SPY")
    _seed("AAPL")
    _stub_requirements(monkeypatch)
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda _symbols, _start, _end: massive.PriceDownloadResult({"SPY": NEW_SERIES, "AAPL": OLD_SERIES}),
    )

    with session_factory()() as session:
        outcome = market_refresh._refresh_locked(session, STALE_NOW, TARGET)
    with session_factory()() as session:
        loaded = arena.load_price_series(
            session,
            _requirements("SPY", "AAPL"),
            {"SPY", "AAPL"},
            now=STALE_NOW,
        )

    assert outcome.complete is False
    assert outcome.updated_symbols == ("AAPL", "SPY")
    assert loaded.status == "stale"
    assert loaded.as_of == TARGET.isoformat()
    assert loaded.stale_symbols == {"AAPL"}


def test_provider_failure_preserves_last_complete_snapshot(monkeypatch):
    _seed("SPY")
    _seed("AAPL")
    _stub_requirements(monkeypatch)
    monkeypatch.setattr(
        massive,
        "download_prices",
        lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
    )

    with session_factory()() as session:
        outcome = market_refresh._refresh_locked(session, UPDATING_NOW, TARGET)
    with session_factory()() as session:
        rows = {row.symbol: row for row in session.scalars(select(PriceCache)).all()}

    assert outcome.complete is False
    assert rows["SPY"].series == OLD_SERIES
    assert rows["AAPL"].series == OLD_SERIES


def test_older_fetch_cannot_overwrite_newer_cache_row():
    _seed("SPY", fetched_at=UPDATING_NOW, series=NEW_SERIES)

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": _series(base_close=99.0)},
            fetched_at=UPDATING_NOW - timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.get(PriceCache, "SPY")

    assert row.series == NEW_SERIES
    assert row.fetched_at == UPDATING_NOW


def test_older_complete_fetch_replaces_newer_lagging_row():
    _seed("SPY", fetched_at=UPDATING_NOW, series=OLD_SERIES)

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": NEW_SERIES},
            fetched_at=UPDATING_NOW - timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.get(PriceCache, "SPY")

    assert row.series == NEW_SERIES
    assert row.end_date == TARGET


def test_newer_narrower_series_cannot_discard_history():
    _seed("SPY", fetched_at=UPDATING_NOW, series=NEW_SERIES)
    narrower = _series(
        start=REQUIRED_START + timedelta(days=14),
        end=TARGET + timedelta(days=1),
        base_close=104.0,
    )

    with session_factory()() as session:
        price_cache.set_cached_series(
            session,
            {"SPY": narrower},
            fetched_at=UPDATING_NOW + timedelta(minutes=1),
        )
    with session_factory()() as session:
        row = session.get(PriceCache, "SPY")

    assert row.series == NEW_SERIES
    assert row.start_date == REQUIRED_START
    assert row.end_date == TARGET
