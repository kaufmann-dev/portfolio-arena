"""Orchestration: load allocations, ensure cached price series, run the
valuation engine per portfolio, and shape metrics for the API.

Nothing here stores NAVs — every request recomputes deterministically from
locked allocations + cached price/FX series (adjusted closes change
retroactively, so recomputation is *more* correct than snapshotting).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import TOO_EARLY_AGE_DAYS
from ..models import Allocation, Portfolio
from . import price_cache, yahoo
from .symbols import cash_currency, fx_pair_for
from .trading_calendar import close_at
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


@dataclass
class PortfolioValuation:
    portfolio: Portfolio
    result: ValuationResult | None
    metrics: dict
    error: str | None = None


@dataclass
class ArenaValuations:
    as_of: str | None
    spy_series: Series  # raw adjusted closes (for identical-window overlays)
    calendar: list[str]
    by_portfolio_id: dict[int, PortfolioValuation] = field(default_factory=dict)


def pricing_symbols(allocations: list[Allocation]) -> set[str]:
    """Yahoo series needed to value these allocations (equities + FX pairs)."""
    symbols: set[str] = set()
    for allocation in allocations:
        for position in allocation.positions:
            currency = cash_currency(position.symbol)
            if currency is None:
                symbols.add(position.symbol)
            elif currency != "USD":
                symbols.add(fx_pair_for(currency))
    return symbols


def load_price_series(session: Session, symbols: list[str], required_start: date) -> dict[str, Series]:
    """Postgres cache first; fetch misses from Yahoo (skipping cooldown symbols)."""
    series = price_cache.get_cached_series(session, symbols, required_start)
    missing = [s for s in symbols if s not in series]
    if missing:
        cooled_down = set(price_cache.recent_failed_symbols(missing))
        to_fetch = [s for s in missing if s not in cooled_down]
        if to_fetch:
            fetched = yahoo.download_prices(to_fetch, required_start)
            price_cache.record_fetch_results(fetched)
            price_cache.set_cached_series(session, fetched, required_start)
            series.update({symbol: data for symbol, data in fetched.items() if yahoo.is_valid_series(data)})
    return series


def _allocation_inputs(allocations: list[Allocation]) -> list[AllocationInput]:
    return [
        AllocationInput(
            effective_date=allocation.effective_date.isoformat(),
            positions=tuple(
                PositionInput(
                    symbol=position.symbol,
                    instrument=position.instrument,
                    weight_pct=float(position.weight_pct),
                    note=position.note,
                )
                for position in allocation.positions
            ),
        )
        for allocation in allocations
    ]


def _as_of(spy_series: Series, now: datetime) -> str | None:
    """Last SPY date whose close has already occurred — today's in-progress
    session is never valued (no intraday prices, no lookahead)."""
    for point in reversed(spy_series):
        day = date.fromisoformat(point["date"])
        if now >= close_at(day):
            return point["date"]
    return None


def load_portfolios(session: Session) -> list[Portfolio]:
    return list(
        session.scalars(
            select(Portfolio).options(
                selectinload(Portfolio.agent),
                selectinload(Portfolio.allocations).selectinload(Allocation.positions),
                selectinload(Portfolio.allocations).selectinload(Allocation.prompt),
            )
        )
    )


def compute_valuations(
    session: Session, portfolios: list[Portfolio], now: datetime | None = None
) -> ArenaValuations:
    """Value the given portfolios (callers pass benchmark-seeded sessions)."""
    now = now or datetime.now(UTC)

    def no_data() -> ArenaValuations:
        empty = ArenaValuations(as_of=None, spy_series=[], calendar=[])
        for portfolio in portfolios:
            empty.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=None, metrics={"has_data": False}
            )
        return empty

    all_allocations = [a for p in portfolios for a in p.allocations]
    if not all_allocations:
        return no_data()

    required_start = min(a.effective_date for a in all_allocations) - timedelta(days=7)
    symbols = pricing_symbols(all_allocations) | {SPY_SYMBOL}
    series = load_price_series(session, sorted(symbols), required_start)

    spy_series = series.get(SPY_SYMBOL) or []
    as_of = _as_of(spy_series, now)
    if as_of is None:
        return no_data()
    calendar = build_calendar(spy_series, as_of)

    valuations = ArenaValuations(as_of=as_of, spy_series=spy_series, calendar=calendar)
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
        except ValuationError as exc:
            logger.warning("cannot value portfolio %s: %s", portfolio.slug, exc)
            valuations.by_portfolio_id[portfolio.id] = PortfolioValuation(
                portfolio=portfolio, result=None, metrics={"has_data": False}, error=str(exc)
            )
    return valuations


def age_days(valuation: PortfolioValuation, as_of: str | None) -> int | None:
    if not valuation.result or not valuation.result.series or as_of is None:
        return None
    inception = date.fromisoformat(valuation.result.series[0]["date"])
    return (date.fromisoformat(as_of) - inception).days


def too_early(age: int | None) -> bool:
    return age is not None and age < TOO_EARLY_AGE_DAYS


def downsample(series: Series, max_points: int = 60) -> list[float]:
    """Sparkline values: evenly sampled NAVs, always keeping the endpoints."""
    navs = [point["nav"] for point in series]
    if len(navs) <= max_points:
        return navs
    step = (len(navs) - 1) / (max_points - 1)
    return [navs[round(i * step)] for i in range(max_points)]
