"""Resilient PostgreSQL price cache for total-return price series."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import MASSIVE_DATA_DELAY_MINUTES, get_settings
from ..models import PriceCache
from .massive import Series, is_valid_series
from .trading_calendar import NY, close_at, is_trading_day


@dataclass(frozen=True)
class CacheEntry:
    series: Series
    start_date: date
    end_date: date
    fetched_at: datetime

    @property
    def dates(self) -> tuple[date, ...]:
        if not is_valid_series(self.series):
            return ()
        try:
            return tuple(sorted(date.fromisoformat(point["date"]) for point in self.series))
        except (KeyError, TypeError, ValueError):
            return ()

    @property
    def earliest_date(self) -> date | None:
        return self.dates[0] if self.dates else None

    @property
    def latest_date(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def covers(self, required_start: date) -> bool:
        earliest = self.earliest_date
        return self.start_date <= required_start and earliest is not None and earliest <= required_start

    def has_session(self, session_date: date) -> bool:
        return session_date in self.dates


def get_cache_entries(session: Session, symbols: list[str]) -> dict[str, CacheEntry]:
    if not symbols:
        return {}

    rows = session.scalars(select(PriceCache).where(PriceCache.symbol.in_(symbols))).all()
    return {
        row.symbol: CacheEntry(
            series=row.series,
            start_date=row.start_date,
            end_date=row.end_date,
            fetched_at=row.fetched_at,
        )
        for row in rows
        if is_valid_series(row.series)
    }


def latest_available_session(now: datetime) -> date:
    """Latest session Massive should expose after its documented 15m delay."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    day = now.astimezone(NY).date()
    delay = timedelta(minutes=MASSIVE_DATA_DELAY_MINUTES)
    while not is_trading_day(day) or now < close_at(day) + delay:
        day -= timedelta(days=1)
    return day


def refresh_due(entry: CacheEntry | None, required_start: date, now: datetime) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if entry is None or not entry.covers(required_start):
        return True
    fetched_at = entry.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    ttl = get_settings().price_cache_ttl_seconds
    if now - fetched_at >= timedelta(seconds=ttl):
        return True
    return not entry.has_session(latest_available_session(now))


def cache_entry(series: Series | None, fetched_at: datetime) -> CacheEntry | None:
    bounds = _series_bounds(series)
    if bounds is None:
        return None
    start_date, end_date = bounds
    return CacheEntry(
        series=series,
        start_date=start_date,
        end_date=end_date,
        fetched_at=fetched_at,
    )


def merge_series(preferred: Series, fallback: Series) -> Series:
    """Fill missing date edges from fallback, preferring refreshed overlaps."""
    merged = {point["date"]: point for point in fallback}
    merged.update({point["date"]: point for point in preferred})
    return [merged[day] for day in sorted(merged)]


def set_cached_series(
    session: Session,
    series_by_symbol: dict[str, Series | None],
    *,
    fetched_at: datetime | None = None,
) -> None:
    cacheable = _cacheable_series(series_by_symbol)
    if not cacheable:
        return

    fetched_at = fetched_at or datetime.now(UTC)
    for symbol, (data, start_date, end_date) in cacheable.items():
        stmt = pg_insert(PriceCache).values(
            symbol=symbol,
            series=data,
            start_date=start_date,
            end_date=end_date,
            fetched_at=fetched_at,
        )
        coverage_improves = and_(
            stmt.excluded.start_date <= PriceCache.start_date,
            stmt.excluded.end_date >= PriceCache.end_date,
            or_(
                stmt.excluded.start_date < PriceCache.start_date,
                stmt.excluded.end_date > PriceCache.end_date,
            ),
        )
        same_coverage_is_newer = and_(
            stmt.excluded.start_date == PriceCache.start_date,
            stmt.excluded.end_date == PriceCache.end_date,
            stmt.excluded.fetched_at >= PriceCache.fetched_at,
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "series": stmt.excluded.series,
                    "start_date": stmt.excluded.start_date,
                    "end_date": stmt.excluded.end_date,
                    "fetched_at": stmt.excluded.fetched_at,
                },
                where=or_(coverage_improves, same_coverage_is_newer),
            )
        )
    session.commit()


def _cacheable_series(
    series_by_symbol: dict[str, Series | None],
) -> dict[str, tuple[Series, date, date]]:
    cacheable: dict[str, tuple[Series, date, date]] = {}
    for symbol, data in series_by_symbol.items():
        bounds = _series_bounds(data)
        if bounds is None:
            continue
        start_date, end_date = bounds
        cacheable[symbol] = (data, start_date, end_date)
    return cacheable


def _series_bounds(series: Series | None) -> tuple[date, date] | None:
    if not is_valid_series(series):
        return None
    try:
        dates = [date.fromisoformat(point["date"]) for point in series]
    except (KeyError, TypeError, ValueError):
        return None
    return min(dates), max(dates)


def clear_cache(session: Session) -> int:
    deleted = session.execute(delete(PriceCache)).rowcount
    session.commit()
    return deleted
