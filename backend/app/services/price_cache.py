"""Two-layer price caching: PostgreSQL rows with a TTL, plus an in-process
failure cooldown so known-bad symbols are not retried immediately.
Both layers are per-process/single-instance by design.

A cached row is only usable when it is fresh *and* its requested start_date
covers the start the caller needs (series grow backwards only when an earlier
inception appears, which is rare)."""
import time
from datetime import UTC, date, datetime, timedelta
from threading import Lock

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import PRICE_FAILURE_COOLDOWN_SECONDS, get_settings
from ..models import PriceCache
from .yahoo import Series, is_valid_series

_failure_cache: dict[str, float] = {}
_failure_cache_lock = Lock()


def get_cached_series(
    session: Session, symbols: list[str], required_start: date
) -> dict[str, Series]:
    if not symbols:
        return {}

    ttl = get_settings().price_cache_ttl_seconds
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl)
    session.execute(delete(PriceCache).where(PriceCache.fetched_at < cutoff))
    rows = session.execute(
        select(PriceCache.symbol, PriceCache.series, PriceCache.start_date).where(
            PriceCache.symbol.in_(symbols),
            PriceCache.fetched_at >= cutoff,
            PriceCache.start_date <= required_start,
        )
    ).all()
    session.commit()

    return {row.symbol: row.series for row in rows if is_valid_series(row.series)}


def set_cached_series(
    session: Session, series_by_symbol: dict[str, Series | None], start: date
) -> None:
    cacheable = {symbol: data for symbol, data in series_by_symbol.items() if is_valid_series(data)}
    if not cacheable:
        return

    for symbol, data in cacheable.items():
        stmt = pg_insert(PriceCache).values(
            symbol=symbol,
            series=data,
            start_date=start,
            fetched_at=datetime.now(UTC),
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "series": stmt.excluded.series,
                    "start_date": stmt.excluded.start_date,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
        )
    session.commit()


def clear_cache(session: Session) -> int:
    deleted = session.execute(delete(PriceCache)).rowcount
    session.commit()
    with _failure_cache_lock:
        _failure_cache.clear()
    return deleted


def recent_failed_symbols(symbols: list[str]) -> list[str]:
    now = time.monotonic()
    recent = []
    with _failure_cache_lock:
        for symbol in symbols:
            failed_at = _failure_cache.get(symbol)
            if failed_at is None:
                continue
            if now - failed_at < PRICE_FAILURE_COOLDOWN_SECONDS:
                recent.append(symbol)
            else:
                _failure_cache.pop(symbol, None)
    return recent


def record_fetch_results(series_by_symbol: dict[str, Series | None]) -> None:
    now = time.monotonic()
    permanent_failures = getattr(series_by_symbol, "permanent_failures", None)
    with _failure_cache_lock:
        for symbol, data in series_by_symbol.items():
            if is_valid_series(data):
                _failure_cache.pop(symbol, None)
            elif permanent_failures is None or symbol in permanent_failures:
                _failure_cache[symbol] = now
            else:
                _failure_cache.pop(symbol, None)
