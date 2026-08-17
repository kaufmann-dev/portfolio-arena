"""Background publication of coherent daily market-data snapshots."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import (
    MARKET_DATA_UPDATE_GRACE_MINUTES,
    MARKET_REFRESH_RETRY_SECONDS,
    MASSIVE_DATA_DELAY_MINUTES,
)
from ..db import get_engine
from . import massive, price_cache
from .trading_calendar import close_at

logger = logging.getLogger(__name__)

# Connection-scoped PostgreSQL advisory lock shared by every web process.
MARKET_REFRESH_LOCK_KEY = 0x504152454E41
READY_POLL_SECONDS = 60


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
        )

    fetch_start = min(
        min(requirements[symbol], entries[symbol].start_date) if symbol in entries else requirements[symbol]
        for symbol in due
    )
    fetched = massive.download_prices(due, fetch_start, target)
    working = dict(entries)
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
        working[symbol] = candidate
        updates[symbol] = merged

    ready_to_publish = _readiness_complete(working, requirements, readiness, target)
    grace_elapsed = now >= close_at(target) + timedelta(
        minutes=MASSIVE_DATA_DELAY_MINUTES + MARKET_DATA_UPDATE_GRACE_MINUTES
    )
    if updates and (ready_to_publish or grace_elapsed):
        # One transaction exposes all successful target-session series together.
        price_cache.set_cached_series(session, updates, fetched_at=now)
        logger.info(
            "published market-data batch target=%s symbols=%d complete=%s",
            target,
            len(updates),
            ready_to_publish,
        )
    elif updates:
        logger.info(
            "withheld incomplete market-data batch target=%s ready=%d/%d",
            target,
            sum(
                1
                for symbol in readiness
                if _entry_ready(working.get(symbol), requirements.get(symbol), target)
            ),
            len(readiness),
        )

    for symbol, error in fetched.errors.items():
        logger.warning("market-data refresh failed symbol=%s error=%s", symbol, type(error).__name__)

    complete = _snapshot_complete(working, requirements, readiness, target)
    return RefreshOutcome(
        acquired=True,
        complete=complete,
        target_as_of=target.isoformat(),
        updated_symbols=tuple(sorted(updates)) if updates and (ready_to_publish or grace_elapsed) else (),
    )


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
