"""Background publication of coherent daily market-data snapshots."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import MARKET_REFRESH_RETRY_SECONDS, PRICE_FETCH_MAX_WORKERS
from ..db import get_engine
from . import massive, price_cache
from .trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

# Connection-scoped PostgreSQL advisory lock shared by every web process.
MARKET_REFRESH_LOCK_KEY = 0x504152454E41
READY_POLL_SECONDS = 60
GROUPED_CATCHUP_MAX_SESSIONS = 5
REGULAR_REFRESH_BATCH_SIZE = PRICE_FETCH_MAX_WORKERS


@dataclass(frozen=True)
class RefreshOutcome:
    acquired: bool
    complete: bool
    target_as_of: str
    updated_symbols: tuple[str, ...] = ()


def market_snapshot(session: Session, now: datetime | None = None):
    """Return the global cache watermark used by the lightweight status API."""
    from .arena import global_pricing_requirements, load_portfolios, load_price_series

    now = _aware(now or datetime.now(UTC))
    target = price_cache.latest_available_session(now)
    requirements, readiness = global_pricing_requirements(load_portfolios(session), target)
    return load_price_series(session, requirements, readiness, now)


def refresh_market_data_once(now: datetime | None = None) -> RefreshOutcome:
    """Fetch due series off the request path and publish one coherent batch."""
    now = _aware(now or datetime.now(UTC))
    target = price_cache.latest_available_session(now)
    engine = get_engine()

    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": MARKET_REFRESH_LOCK_KEY},
            ).scalar_one()
        )
        connection.commit()
        if not acquired:
            return RefreshOutcome(False, False, target.isoformat())

        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                return _refresh_locked(session, now, target)
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MARKET_REFRESH_LOCK_KEY},
            )
            connection.commit()


def _refresh_locked(session: Session, now: datetime, target) -> RefreshOutcome:
    from .arena import global_pricing_requirements, load_portfolios

    requirements, readiness = global_pricing_requirements(load_portfolios(session), target)
    symbols = sorted(requirements)
    entries = price_cache.get_cache_entries(session, symbols)
    updated_symbols = _refresh_grouped_sessions(
        session,
        now,
        target,
        requirements,
        readiness,
        entries,
    )
    entries = price_cache.get_cache_entries(session, symbols)
    due = [
        symbol
        for symbol in symbols
        if price_cache.refresh_due(entries.get(symbol), requirements[symbol], now)
    ]
    if not due:
        return RefreshOutcome(
            True,
            _snapshot_complete(entries, requirements, readiness, target),
            target.isoformat(),
            tuple(sorted(updated_symbols)),
        )

    due = sorted(
        due,
        key=lambda symbol: (
            symbol != "SPY",
            symbol not in readiness,
            symbol,
        ),
    )[:REGULAR_REFRESH_BATCH_SIZE]
    fetch_start = min(
        min(requirements[symbol], entries[symbol].start_date) if symbol in entries else requirements[symbol]
        for symbol in due
    )
    fetched = massive.download_prices(due, fetch_start, target)
    updates: dict[str, massive.Series | None] = {}

    for symbol in due:
        data = fetched.get(symbol)
        if not massive.is_valid_series(data):
            continue
        current = entries.get(symbol)
        merged = price_cache.merge_series(data, current.series) if current else data
        candidate = price_cache.cache_entry(merged, now)
        if candidate is None:
            continue
        updates[symbol] = merged
        if current is None or merged != current.series:
            updated_symbols.add(symbol)

    if updates:
        price_cache.set_cached_series(session, updates, fetched_at=now)
        logger.info(
            "published regular market-data batch target=%s symbols=%d",
            target,
            len(updates),
        )

    for symbol, error in fetched.errors.items():
        logger.warning("market-data refresh failed symbol=%s error=%s", symbol, type(error).__name__)

    entries = price_cache.get_cache_entries(session, symbols)
    complete = _snapshot_complete(entries, requirements, readiness, target)
    return RefreshOutcome(
        acquired=True,
        complete=complete,
        target_as_of=target.isoformat(),
        updated_symbols=tuple(sorted(updated_symbols)),
    )


def _refresh_grouped_sessions(
    session: Session,
    now: datetime,
    target: date,
    requirements: dict[str, date],
    readiness: set[str],
    entries: dict[str, price_cache.CacheEntry],
) -> set[str]:
    """Append recent closes for live symbols using one market-wide response per session."""
    working = dict(entries)
    candidates: set[str] = set()
    sessions: set[date] = set()
    for symbol in readiness:
        entry = working.get(symbol)
        required_start = requirements.get(symbol)
        if entry is None or required_start is None or not entry.covers(required_start):
            continue
        missing = _forward_sessions(entry.latest_date, target)
        if 0 < len(missing) <= GROUPED_CATCHUP_MAX_SESSIONS:
            candidates.add(symbol)
            sessions.update(missing)

    updated: set[str] = set()
    for session_date in sorted(sessions):
        pending = sorted(
            symbol
            for symbol in candidates
            if (latest := working[symbol].latest_date) is not None and latest < session_date
        )
        if not pending:
            continue
        try:
            grouped = massive.download_grouped_session(pending, session_date)
        except massive.MassiveError as exc:
            logger.warning(
                "grouped market-data refresh failed target=%s error=%s",
                session_date,
                type(exc).__name__,
            )
            break

        batch: dict[str, massive.Series | None] = {}
        for symbol in pending:
            points = grouped.prices.get(symbol)
            if not massive.is_valid_series(points):
                continue
            current = working[symbol]
            historical = _apply_historical_factor(
                current.series,
                grouped.historical_factors.get(symbol, 1.0),
                session_date,
            )
            merged = price_cache.merge_series(points, historical)
            candidate = price_cache.cache_entry(merged, now)
            if candidate is None:
                continue
            working[symbol] = candidate
            batch[symbol] = merged
            if merged != current.series:
                updated.add(symbol)

        if batch:
            price_cache.set_cached_series(session, batch, fetched_at=now)
            logger.info(
                "published grouped market-data session=%s symbols=%d requested=%d",
                session_date,
                len(batch),
                len(pending),
            )

    return updated


def _forward_sessions(latest: date | None, target: date) -> list[date]:
    if latest is None or latest >= target:
        return []
    sessions: list[date] = []
    day = latest + timedelta(days=1)
    while day <= target:
        if is_trading_day(day):
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def _apply_historical_factor(series: massive.Series, factor: float, session_date: date) -> massive.Series:
    if factor == 1.0:
        return series
    cutoff = session_date.isoformat()
    return [
        {
            "date": point["date"],
            "close": round(float(point["close"]) * factor, 6)
            if str(point["date"]) < cutoff
            else point["close"],
        }
        for point in series
    ]


def _snapshot_complete(entries, requirements, readiness, target) -> bool:
    return all(
        (entry := entries.get(symbol)) is not None and entry.covers(required_start)
        for symbol, required_start in requirements.items()
    ) and _readiness_complete(entries, requirements, readiness, target)


def _readiness_complete(entries, requirements, readiness, target) -> bool:
    return all(_entry_ready(entries.get(symbol), requirements.get(symbol), target) for symbol in readiness)


def _entry_ready(entry, required_start, target) -> bool:
    return bool(
        entry is not None
        and required_start is not None
        and entry.covers(required_start)
        and entry.has_session(target)
    )


async def run_market_refresh_loop(stop: asyncio.Event) -> None:
    """Refresh immediately, then use short publication-lag retries."""
    retry_index = 0
    while not stop.is_set():
        delay = READY_POLL_SECONDS
        try:
            outcome = await asyncio.to_thread(refresh_market_data_once)
            if outcome.complete:
                retry_index = 0
            else:
                delay = MARKET_REFRESH_RETRY_SECONDS[min(retry_index, len(MARKET_REFRESH_RETRY_SECONDS) - 1)]
                retry_index += 1
        except Exception:
            logger.exception("background market-data refresh failed")
            delay = MARKET_REFRESH_RETRY_SECONDS[-1]

        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
