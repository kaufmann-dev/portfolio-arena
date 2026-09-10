"""Version-scoped orchestration for deterministic open/close analytics.

Only prices and decisions persist. Exact-input caches reuse pure computations;
all public reads stay independent of evaluator enablement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import MARKET_DATA_UPDATE_GRACE_MINUTES, MASSIVE_DATA_DELAY_MINUTES
from ..models import Agent, Allocation, ModelDefinition, Portfolio, Signal
from . import price_cache
from .analysis_cache import SingleFlightLru, fingerprint
from .rebuilt import (
    HORIZONS,
    PolicyResult,
    SignalInput,
    evaluate_policy_grid,
    prepare_market,
    signal_horizon_statistics,
)
from .trading_calendar import NY, boundary_at, boundary_value, is_trading_day
from .valuation import (
    AllocationInput,
    Boundary,
    PositionInput,
    Series,
    ValuationError,
    ValuationResult,
    boundary_date,
    build_calendar,
    compute_metrics,
    value_portfolio,
)

SPY_SYMBOL = "SPY"
MarketDataStatus = Literal["fresh", "updating", "stale", "unavailable"]
ANALYTICS_ENGINE_VERSION = 2


@dataclass
class PortfolioValuation:
    portfolio: Portfolio
    result: ValuationResult | None
    metrics: dict
    error: str | None = None


@dataclass
class ArenaValuations:
    as_of: Boundary | None
    current_date: date
    market_data_status: MarketDataStatus
    spy_series: Series
    calendar: list[Boundary]
    by_portfolio_id: dict[int, PortfolioValuation] = field(default_factory=dict)


@dataclass
class PriceSeriesLoad:
    series: dict[str, Series]
    status: MarketDataStatus
    as_of: Boundary | None
    target_as_of: Boundary
    stale_symbols: set[str] = field(default_factory=set)
    unavailable_symbols: set[str] = field(default_factory=set)


@dataclass
class RebuiltPortfolioAnalysis:
    portfolio: Portfolio
    signal_horizons: list[dict]
    policies: dict[float, PolicyResult]
    selected: PolicyResult | None
    error: str | None = None
    stale_data: bool = False
    frozen_symbols: list[str] = field(default_factory=list)


@dataclass
class RebuiltArena:
    as_of: Boundary | None
    market_data_status: MarketDataStatus
    spy_series: Series
    calendar: list[Boundary]
    by_portfolio_id: dict[int, RebuiltPortfolioAnalysis] = field(default_factory=dict)


_managed_cache: SingleFlightLru[str, tuple] = SingleFlightLru(max_entries=256)
_rebuilt_cache: SingleFlightLru[str, tuple] = SingleFlightLru(max_entries=256)


def clear_analysis_caches() -> None:
    _managed_cache.clear()
    _rebuilt_cache.clear()


def pricing_requirements(allocations: list[Allocation]) -> dict[str, date]:
    requirements = {SPY_SYMBOL: min(allocation.effective_date for allocation in allocations)}
    for allocation in allocations:
        for position in allocation.positions:
            requirements[position.symbol] = min(
                requirements.get(position.symbol, allocation.effective_date), allocation.effective_date
            )
    return requirements


def signal_pricing_requirements(signals: list[Signal], fallback_start: date) -> dict[str, date]:
    requirements = {SPY_SYMBOL: min((signal.effective_date for signal in signals), default=fallback_start)}
    for signal in signals:
        for position in signal.positions:
            requirements[position.symbol] = min(
                requirements.get(position.symbol, signal.effective_date), signal.effective_date
            )
    return requirements


def merge_pricing_requirements(*groups: dict[str, date]) -> dict[str, date]:
    merged: dict[str, date] = {}
    for group in groups:
        for symbol, start in group.items():
            merged[symbol] = min(merged.get(symbol, start), start)
    return merged


def managed_readiness_symbols(
    portfolios: list[Portfolio], target: date, target_phase: str = "close"
) -> set[str]:
    symbols = {SPY_SYMBOL}
    for portfolio in portfolios:
        latest = max(
            (
                item
                for item in portfolio.allocations
                if item.effective_date < target
                or (
                    item.effective_date == target
                    and (portfolio.execution_boundary == "open" or target_phase == "close")
                )
            ),
            key=lambda item: (item.effective_date, item.id),
            default=None,
        )
        if latest:
            symbols.update(position.symbol for position in latest.positions)
    return symbols


def rebuilt_readiness_symbols(portfolios: list[Portfolio], target: date) -> set[str]:
    symbols = {SPY_SYMBOL}
    earliest = target
    remaining = int(max(HORIZONS))
    while remaining:
        earliest -= timedelta(days=1)
        if is_trading_day(earliest):
            remaining -= 1
    for portfolio in portfolios:
        for signal in portfolio.signals:
            if earliest <= signal.effective_date <= target:
                symbols.update(position.symbol for position in signal.positions)
    return symbols


def global_pricing_requirements(
    portfolios: list[Portfolio], target: date, target_phase: str = "close"
) -> tuple[dict[str, date], set[str]]:
    managed = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "managed"]
    rebuilt = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "rebuilt"]
    allocations = [
        item for portfolio in managed for item in portfolio.allocations if item.effective_date <= target
    ]
    signals = [item for portfolio in rebuilt for item in portfolio.signals if item.effective_date <= target]
    requirements = merge_pricing_requirements(
        pricing_requirements(allocations) if allocations else {},
        signal_pricing_requirements(signals, target - timedelta(days=45))
        if signals or not allocations
        else {},
    )
    readiness = managed_readiness_symbols(managed, target, target_phase) | rebuilt_readiness_symbols(
        rebuilt, target
    )
    return requirements, readiness


def load_price_series(
    session: Session,
    required_starts: dict[str, date],
    readiness_symbols: set[str] | None = None,
    now: datetime | None = None,
) -> PriceSeriesLoad:
    """Read a coherent version-local boundary watermark without provider I/O."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    target = price_cache.latest_available_boundary(now)
    entries = price_cache.get_cache_entries(session, sorted(required_starts))
    series = {symbol: price_cache.published_series(entry.series, now) for symbol, entry in entries.items()}
    unavailable = {
        symbol
        for symbol, start in required_starts.items()
        if symbol not in entries or not entries[symbol].covers(start)
    }
    readiness = set(readiness_symbols if readiness_symbols is not None else required_starts) | {SPY_SYMBOL}
    observed: dict[str, dict[str, Boundary]] = {}
    for symbol in readiness:
        observed[symbol] = {
            event["timestamp"]: event
            for point in series.get(symbol, [])
            for phase in ("open", "close")
            if point.get(phase) is not None
            and (event := boundary_value(date.fromisoformat(point["date"]), phase))["timestamp"]
            <= target["timestamp"]
        }
    lagging = {symbol for symbol in readiness if target["timestamp"] not in observed[symbol]}
    common = set.intersection(*(set(points) for points in observed.values())) if observed else set()
    latest = max(common, default=None)
    as_of = observed[SPY_SYMBOL].get(latest) if latest else None
    deadline = boundary_at(date.fromisoformat(boundary_date(target)), target["phase"]) + timedelta(
        minutes=MASSIVE_DATA_DELAY_MINUTES + MARKET_DATA_UPDATE_GRACE_MINUTES
    )
    status: MarketDataStatus = (
        "unavailable"
        if unavailable or not as_of
        else "fresh"
        if not lagging
        else "updating"
        if now < deadline
        else "stale"
    )
    return PriceSeriesLoad(
        series, status, as_of, target, lagging if status == "stale" else set(), unavailable
    )


def load_portfolios(session: Session, version_id: int | None = None) -> list[Portfolio]:
    query = select(Portfolio).options(
        selectinload(Portfolio.agent).selectinload(Agent.model).selectinload(ModelDefinition.capabilities),
        selectinload(Portfolio.prompt),
        selectinload(Portfolio.version),
        selectinload(Portfolio.allocations).selectinload(Allocation.positions),
        selectinload(Portfolio.signals).selectinload(Signal.positions),
    )
    if version_id is not None:
        query = query.where(Portfolio.version_id == version_id)
    return list(session.scalars(query))


def _inputs(items: list) -> list[dict]:
    return [
        {
            "effective_date": item.effective_date.isoformat(),
            "positions": tuple(
                PositionInput(position.symbol, float(position.weight_pct), position.note)
                for position in item.positions
            ),
        }
        for item in items
    ]


def _cache_key(
    portfolio: Portfolio, items: list, prices: dict[str, Series], calendar: list[Boundary], as_of: Boundary
) -> str:
    symbols = {SPY_SYMBOL, *(position.symbol for item in items for position in item.positions)}
    return fingerprint(
        {
            "engine": ANALYTICS_ENGINE_VERSION,
            "portfolio_id": portfolio.id,
            "version_id": portfolio.version_id,
            "mode": portfolio.prompt_mode,
            "direction": portfolio.direction,
            "execution_boundary": portfolio.execution_boundary,
            "as_of": as_of,
            "calendar": calendar,
            "decisions": [
                {
                    "id": item.id,
                    "date": item.effective_date.isoformat(),
                    "positions": [
                        (position.symbol, float(position.weight_pct), position.note)
                        for position in item.positions
                    ],
                }
                for item in items
            ],
            "prices": {symbol: prices.get(symbol, []) for symbol in sorted(symbols)},
        }
    )


def compute_valuations(
    session: Session, portfolios: list[Portfolio], now: datetime | None = None
) -> ArenaValuations:
    portfolios = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "managed"]
    now = now or datetime.now(UTC)
    target = price_cache.latest_available_session(now)
    allocations = [
        item for portfolio in portfolios for item in portfolio.allocations if item.effective_date <= target
    ]
    if not allocations:
        return ArenaValuations(
            None,
            now.astimezone(NY).date(),
            "fresh",
            [],
            [],
            {
                portfolio.id: PortfolioValuation(portfolio, None, {"has_data": False})
                for portfolio in portfolios
            },
        )
    requirements = pricing_requirements(allocations)
    loaded = load_price_series(
        session,
        requirements,
        managed_readiness_symbols(portfolios, target, price_cache.latest_available_boundary(now)["phase"]),
        now,
    )
    spy = loaded.series.get(SPY_SYMBOL, [])
    calendar = build_calendar(spy, loaded.as_of) if loaded.as_of else []
    arena = ArenaValuations(loaded.as_of, now.astimezone(NY).date(), loaded.status, spy, calendar)
    for portfolio in portfolios:
        if not loaded.as_of or not portfolio.allocations:
            arena.by_portfolio_id[portfolio.id] = PortfolioValuation(portfolio, None, {"has_data": False})
            continue

        def build(portfolio=portfolio):
            try:
                result = value_portfolio(
                    [AllocationInput(**item) for item in _inputs(portfolio.allocations)],
                    loaded.series,
                    calendar,
                    loaded.as_of,
                    portfolio.direction,
                    portfolio.execution_boundary,
                )
                return result, compute_metrics(result, spy, portfolio.direction), None
            except ValuationError as exc:
                return None, {"has_data": False}, str(exc)

        result, metrics, error = _managed_cache.get_or_compute(
            _cache_key(portfolio, portfolio.allocations, loaded.series, calendar, loaded.as_of), build
        )
        arena.by_portfolio_id[portfolio.id] = PortfolioValuation(portfolio, result, metrics, error)
        if error:
            arena.market_data_status = "unavailable"
    return arena


def compute_rebuilt_arena(
    session: Session, portfolios: list[Portfolio], now: datetime | None = None
) -> RebuiltArena:
    portfolios = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "rebuilt"]
    now = now or datetime.now(UTC)
    target = price_cache.latest_available_session(now)
    signals = [
        item for portfolio in portfolios for item in portfolio.signals if item.effective_date <= target
    ]
    loaded = load_price_series(
        session,
        signal_pricing_requirements(signals, target - timedelta(days=45)),
        rebuilt_readiness_symbols(portfolios, target),
        now,
    )
    spy = loaded.series.get(SPY_SYMBOL, [])
    calendar = build_calendar(spy, loaded.as_of) if loaded.as_of else []
    arena = RebuiltArena(loaded.as_of, loaded.status, spy, calendar)
    market = prepare_market(loaded.series, calendar)
    for portfolio in portfolios:
        if not loaded.as_of:
            pending_inputs = [
                SignalInput(item.id, item.effective_date.isoformat(), ()) for item in portfolio.signals
            ]
            horizons = [signal_horizon_statistics(pending_inputs, {}, [], horizon) for horizon in HORIZONS]
            arena.by_portfolio_id[portfolio.id] = RebuiltPortfolioAnalysis(portfolio, horizons, {}, None)
            continue

        def build(portfolio=portfolio):
            try:
                inputs = [
                    SignalInput(item.id, **payload)
                    for item, payload in zip(portfolio.signals, _inputs(portfolio.signals), strict=True)
                ]
                horizons, policies, selected = evaluate_policy_grid(
                    inputs,
                    loaded.series,
                    calendar,
                    portfolio.direction,
                    portfolio.execution_boundary,
                    prepared_market=market,
                )
                return horizons, {policy.horizon: policy for policy in policies}, selected, None
            except ValuationError as exc:
                return [], {}, None, str(exc)

        horizons, policies, selected, error = _rebuilt_cache.get_or_compute(
            _cache_key(portfolio, portfolio.signals, loaded.series, calendar, loaded.as_of), build
        )
        arena.by_portfolio_id[portfolio.id] = RebuiltPortfolioAnalysis(
            portfolio, horizons, policies, selected, error, loaded.status == "stale"
        )
        if error:
            arena.market_data_status = "unavailable"
    return arena


def age_days(valuation: PortfolioValuation, current_date: date) -> int | None:
    return (
        (current_date - date.fromisoformat(boundary_date(valuation.result.series[0]))).days
        if valuation.result and valuation.result.series
        else None
    )


def downsample(series: Series, max_points: int = 60) -> list[float]:
    navs = [point["nav"] for point in series]
    if len(navs) <= max_points:
        return navs
    return [navs[round(index * (len(navs) - 1) / (max_points - 1))] for index in range(max_points)]
