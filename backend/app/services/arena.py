"""Orchestration: load allocations, ensure cached price series, run the
valuation engine per portfolio, and shape metrics for the API.

Nothing here stores NAVs — every request recomputes deterministically from
locked allocations + cached total-return series (corporate-action adjustments
change retroactively, so recomputation is *more* correct than snapshotting).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import TOO_EARLY_AGE_DAYS
from ..models import Agent, Allocation, ModelDefinition, Portfolio
from . import massive, price_cache
from .trading_calendar import NY
from .valuation import (
    AllocationInput,
    PositionInput,
    Series,
    ValuationError,
    ValuationResult,
    build_calendar,
    compute_metrics,
    value_portfolio,
)

logger = logging.getLogger(__name__)

SPY_SYMBOL = "SPY"
MarketDataStatus = Literal["fresh", "stale", "unavailable"]


@dataclass
class PortfolioValuation:
    portfolio: Portfolio
    result: ValuationResult | None
    metrics: dict
    error: str | None = None


@dataclass
class ArenaValuations:
    as_of: str | None
    current_date: date
    market_data_status: MarketDataStatus
    spy_series: Series  # raw total-return closes (for identical-window overlays)
    calendar: list[str]
    by_portfolio_id: dict[int, PortfolioValuation] = field(default_factory=dict)


@dataclass
class PriceSeriesLoad:
    series: dict[str, Series]
    status: MarketDataStatus
    stale_symbols: set[str] = field(default_factory=set)
    unavailable_symbols: set[str] = field(default_factory=set)


def pricing_requirements(allocations: list[Allocation]) -> dict[str, date]:
    """Earliest date each Massive series must cover for deterministic valuation."""
    earliest_allocation = min(allocation.effective_date for allocation in allocations)
    requirements = {SPY_SYMBOL: earliest_allocation}
    for allocation in allocations:
        for position in allocation.positions:
            current = requirements.get(position.symbol)
            if current is None or allocation.effective_date < current:
                requirements[position.symbol] = allocation.effective_date
    return requirements


def load_price_series(
    session: Session,
    required_starts: dict[str, date],
    now: datetime | None = None,
) -> PriceSeriesLoad:
    """Refresh due rows without discarding the last usable cached series."""
    now = now or datetime.now(UTC)
    symbols = sorted(required_starts)
    if not symbols:
        return PriceSeriesLoad(series={}, status="fresh")

    entries = price_cache.get_cache_entries(session, symbols)
    series: dict[str, Series] = {}
    stale_symbols: set[str] = set()
    unavailable_symbols: set[str] = set()
    latest_available = price_cache.latest_available_session(now)

    due = [
        symbol
        for symbol in symbols
        if price_cache.refresh_due(entries.get(symbol), required_starts[symbol], now)
    ]
    cooled_down = set(price_cache.recent_failed_symbols(due))
    to_fetch = [symbol for symbol in due if symbol not in cooled_down]
    accepted: dict[str, price_cache.CacheEntry] = {}
    successful: set[str] = set()
    failed: set[str] = set()

    if to_fetch:
        fetch_start = min(
            min(required_starts[symbol], entries[symbol].start_date)
            if symbol in entries
            else required_starts[symbol]
            for symbol in to_fetch
        )
        fetched = massive.download_prices(to_fetch, fetch_start, latest_available)
        complete_updates: dict[str, Series | None] = {}
        lagging_updates: dict[str, Series | None] = {}
        lagging_inserts: dict[str, Series | None] = {}

        for symbol in to_fetch:
            data = fetched.get(symbol)
            candidate = price_cache.cache_entry(data, now)
            covers_history = bool(candidate and candidate.covers(required_starts[symbol]))
            refresh_complete = bool(candidate and candidate.has_session(latest_available))

            if covers_history and refresh_complete:
                accepted[symbol] = candidate
                complete_updates[symbol] = data
                successful.add(symbol)
                continue

            failed.add(symbol)
            current = entries.get(symbol)
            # A lagging but historically complete response remains useful
            # last-known data. Preserve a current row when it already covers
            # history; otherwise retain both non-overlapping coverage edges.
            if covers_history and not (current and current.covers(required_starts[symbol])):
                if current:
                    merged = price_cache.merge_series(candidate.series, current.series)
                    retained = price_cache.cache_entry(
                        merged,
                        price_cache.expired_fetched_at(now),
                    )
                    if retained and retained.covers(required_starts[symbol]):
                        accepted[symbol] = retained
                        lagging_updates[symbol] = merged
                else:
                    accepted[symbol] = candidate
                    lagging_inserts[symbol] = data

        price_cache.record_refresh_results(successful, failed)
        price_cache.set_cached_series(session, complete_updates, fetched_at=now)
        price_cache.set_cached_series(
            session,
            lagging_updates,
            fetched_at=price_cache.expired_fetched_at(now),
        )
        price_cache.insert_cached_series_if_missing(
            session,
            lagging_inserts,
            fetched_at=now,
        )

    for symbol in symbols:
        entry = accepted.get(symbol) or entries.get(symbol)
        if not entry or not entry.covers(required_starts[symbol]):
            if entry and massive.is_valid_series(entry.series):
                series[symbol] = entry.series
            unavailable_symbols.add(symbol)
            continue

        series[symbol] = entry.series
        if symbol in due and symbol not in successful:
            stale_symbols.add(symbol)

    status: MarketDataStatus = "fresh"
    if unavailable_symbols:
        status = "unavailable"
    elif stale_symbols:
        status = "stale"
    return PriceSeriesLoad(
        series=series,
        status=status,
        stale_symbols=stale_symbols,
        unavailable_symbols=unavailable_symbols,
    )


def _allocation_inputs(allocations: list[Allocation]) -> list[AllocationInput]:
    return [
        AllocationInput(
            effective_date=allocation.effective_date.isoformat(),
            positions=tuple(
                PositionInput(
                    symbol=position.symbol,
                    weight_pct=float(position.weight_pct),
                    note=position.note,
                )
                for position in allocation.positions
            ),
        )
        for allocation in allocations
    ]


def _as_of(spy_series: Series, latest_available: date) -> str | None:
    """Last SPY close Massive should expose after its availability delay."""
    available = [
        point["date"] for point in spy_series if date.fromisoformat(point["date"]) <= latest_available
    ]
    return max(available, default=None)


def load_portfolios(session: Session) -> list[Portfolio]:
    return list(
        session.scalars(
            select(Portfolio).options(
                selectinload(Portfolio.agent)
                .selectinload(Agent.model)
                .selectinload(ModelDefinition.capabilities),
                selectinload(Portfolio.prompt),
                selectinload(Portfolio.allocations).selectinload(Allocation.positions),
            )
        )
    )


def compute_valuations(
    session: Session, portfolios: list[Portfolio], now: datetime | None = None
) -> ArenaValuations:
    """Value the given portfolios (callers pass benchmark-seeded sessions)."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    current_date = now.astimezone(NY).date()

    def no_data(status: MarketDataStatus = "fresh") -> ArenaValuations:
        empty = ArenaValuations(
            as_of=None,
            current_date=current_date,
            market_data_status=status,
            spy_series=[],
            calendar=[],
        )
        for portfolio in portfolios:
            empty.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=None, metrics={"has_data": False}
            )
        return empty

    all_allocations = [a for p in portfolios for a in p.allocations]
    if not all_allocations:
        return no_data()

    requirements = pricing_requirements(all_allocations)
    price_load = load_price_series(session, requirements, now)
    series = price_load.series

    spy_series = series.get(SPY_SYMBOL) or []
    as_of = _as_of(spy_series, price_cache.latest_available_session(now))
    if as_of is None:
        return no_data(price_load.status)
    calendar = build_calendar(spy_series, as_of)

    valuations = ArenaValuations(
        as_of=as_of,
        current_date=current_date,
        market_data_status=price_load.status,
        spy_series=spy_series,
        calendar=calendar,
    )
    for portfolio in portfolios:
        if not portfolio.allocations:
            valuations.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=None, metrics={"has_data": False}
            )
            continue
        try:
            result = value_portfolio(
                _allocation_inputs(portfolio.allocations),
                cost_bps=portfolio.cost_bps,
                prices=series,
                calendar=calendar,
                as_of=as_of,
            )
            metrics = compute_metrics(result, spy_series)
            valuations.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=result, metrics=metrics
            )
            if (result.stale_days or result.frozen_symbols) and valuations.market_data_status == "fresh":
                valuations.market_data_status = "stale"
        except ValuationError as exc:
            logger.warning("cannot value portfolio %s: %s", portfolio.slug, exc)
            valuations.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=None, metrics={"has_data": False}, error=str(exc)
            )
            valuations.market_data_status = "unavailable"
    return valuations


def age_days(valuation: PortfolioValuation, current_date: date) -> int | None:
    if not valuation.result or not valuation.result.series:
        return None
    inception = date.fromisoformat(valuation.result.series[0]["date"])
    return (current_date - inception).days


def too_early(age: int | None) -> bool:
    return age is not None and age < TOO_EARLY_AGE_DAYS


def downsample(series: Series, max_points: int = 60) -> list[float]:
    """Sparkline values: evenly sampled NAVs, always keeping the endpoints."""
    navs = [point["nav"] for point in series]
    if len(navs) <= max_points:
        return navs
    step = (len(navs) - 1) / (max_points - 1)
    return [navs[round(i * step)] for i in range(max_points)]
