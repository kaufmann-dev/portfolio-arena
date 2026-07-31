"""Orchestration: load allocations, ensure cached price series, run the
valuation engine per portfolio, and shape metrics for the API.

Nothing here stores NAVs — every request recomputes deterministically from
locked allocations + cached total-return series (corporate-action adjustments
change retroactively, so recomputation is *more* correct than snapshotting).
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, Allocation, ModelDefinition, Portfolio, Signal
from . import massive, price_cache
from .rebuilt import (
    EXPOSURES,
    HORIZONS,
    CostBasis,
    Objective,
    PolicyResult,
    RebuiltValuationError,
    SignalInput,
    evaluate_policy_grid,
    hac_mean_statistics,
    selected_objective_score,
)
from .trading_calendar import NY
from .valuation import (
    FROZEN_AFTER_TRADING_DAYS,
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


@dataclass
class RebuiltPortfolioAnalysis:
    portfolio: Portfolio
    signal_horizons: list[dict]
    policies: dict[tuple[int, int], PolicyResult]
    selected: PolicyResult | None
    error: str | None = None
    stale_data: bool = False
    frozen_symbols: list[str] = field(default_factory=list)


@dataclass
class RebuiltArena:
    as_of: str | None
    market_data_status: MarketDataStatus
    spy_series: Series
    calendar: list[str]
    objective: Objective
    cost_basis: CostBasis
    by_portfolio_id: dict[int, RebuiltPortfolioAnalysis] = field(default_factory=dict)
    common_policy: dict | None = None
    common_member_ids: set[int] = field(default_factory=set)
    common_metrics_by_portfolio_id: dict[int, dict] = field(default_factory=dict)
    common_series_by_portfolio_id: dict[int, Series] = field(default_factory=dict)
    common_spy_series: Series = field(default_factory=list)
    common_meta_series: Series = field(default_factory=list)


@dataclass
class _CommonCandidate:
    metrics: dict
    member_metrics: dict[int, dict]
    member_series: dict[int, Series]
    meta_series: Series
    spy_series: Series


@dataclass
class _CommonSelection:
    policy: dict | None = None
    member_ids: set[int] = field(default_factory=set)
    member_metrics: dict[int, dict] = field(default_factory=dict)
    member_series: dict[int, Series] = field(default_factory=dict)
    spy_series: Series = field(default_factory=list)
    meta_series: Series = field(default_factory=list)


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


def signal_pricing_requirements(signals: list[Signal], fallback_start: date) -> dict[str, date]:
    requirements = {
        SPY_SYMBOL: min(
            (signal.effective_date for signal in signals),
            default=fallback_start,
        )
    }
    for signal in signals:
        for position in signal.positions:
            current = requirements.get(position.symbol)
            if current is None or signal.effective_date < current:
                requirements[position.symbol] = signal.effective_date
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
                selectinload(Portfolio.signals).selectinload(Signal.positions),
            )
        )
    )


def compute_valuations(
    session: Session, portfolios: list[Portfolio], now: datetime | None = None
) -> ArenaValuations:
    """Value managed portfolios from their preserved allocation history."""
    portfolios = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "managed"]
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


def downsample(series: Series, max_points: int = 60) -> list[float]:
    """Sparkline values: evenly sampled NAVs, always keeping the endpoints."""
    navs = [point["nav"] for point in series]
    if len(navs) <= max_points:
        return navs
    step = (len(navs) - 1) / (max_points - 1)
    return [navs[round(i * step)] for i in range(max_points)]


def _signal_inputs(signals: list[Signal]) -> list[SignalInput]:
    return [
        SignalInput(
            id=signal.id,
            effective_date=signal.effective_date.isoformat(),
            positions=tuple(
                PositionInput(
                    symbol=position.symbol,
                    weight_pct=float(position.weight_pct),
                    note=position.note,
                )
                for position in signal.positions
            ),
        )
        for signal in signals
    ]


def _rankable_common_members(
    analyses: dict[int, RebuiltPortfolioAnalysis],
) -> list[RebuiltPortfolioAnalysis]:
    members = []
    for analysis in analyses.values():
        if analysis.portfolio.status != "active":
            continue
        if (
            analysis.error is not None
            or len(analysis.signal_horizons) != len(HORIZONS)
            or any(
                (horizon, exposure) not in analysis.policies for horizon in HORIZONS for exposure in EXPOSURES
            )
        ):
            continue
        if not analysis.portfolio.founding_v2:
            horizon_20 = next(item for item in analysis.signal_horizons if item["horizon"] == 20)
            if not horizon_20["eligible"]:
                continue
        members.append(analysis)
    return members


def _common_admitted_member_ids(
    analyses: dict[int, RebuiltPortfolioAnalysis],
) -> set[int]:
    admitted: set[int] = set()
    for analysis in analyses.values():
        if analysis.portfolio.status != "active":
            continue
        if analysis.portfolio.founding_v2:
            admitted.add(analysis.portfolio.id)
            continue
        horizon_20 = next(
            (item for item in analysis.signal_horizons if item["horizon"] == 20),
            None,
        )
        if horizon_20 and horizon_20["eligible"]:
            admitted.add(analysis.portfolio.id)
    return admitted


def _daily_metrics(
    strategy_returns: list[float],
    spy_returns: list[float],
    dates: list[str],
    *,
    baseline: str,
    horizon: int,
    family_size: int,
    turnover_pct: float = 0.0,
    cost_drag_pct: float = 0.0,
) -> dict:
    alphas = [strategy - spy for strategy, spy in zip(strategy_returns, spy_returns, strict=True)]
    alpha_stats = hac_mean_statistics(alphas, lag=horizon - 1, family_size=family_size)
    strategy_std = _std(strategy_returns)
    alpha_std = _std(alphas)
    mean_return = sum(strategy_returns) / len(strategy_returns)
    nav = 100.0
    spy_nav = 100.0
    peak = nav
    max_drawdown = 0.0
    for strategy_return, spy_return in zip(strategy_returns, spy_returns, strict=True):
        nav *= 1.0 + strategy_return
        spy_nav *= 1.0 + spy_return
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    return {
        "has_data": True,
        "horizon": horizon,
        "start_date": baseline,
        "end_date": dates[-1],
        "itd_return": nav / 100.0 - 1.0,
        "spy_return": spy_nav / 100.0 - 1.0,
        "cumulative_excess": (nav - spy_nav) / 100.0,
        "ann_volatility": strategy_std * math.sqrt(252),
        "sharpe": (mean_return / strategy_std * math.sqrt(252) if strategy_std > 0 else None),
        "information_ratio": (
            alpha_stats["mean_daily_alpha"] / alpha_std * math.sqrt(252) if alpha_std > 0 else None
        ),
        "max_drawdown": max_drawdown,
        "turnover_pct": turnover_pct,
        "cost_drag_pct": cost_drag_pct,
        "eligible": True,
        **alpha_stats,
    }


def _common_horizon_eligibility(
    members: list[RebuiltPortfolioAnalysis],
) -> dict[int, bool]:
    eligibility = {}
    for horizon in HORIZONS:
        stats = [
            next(item for item in member.signal_horizons if item["horizon"] == horizon) for member in members
        ]
        complete = sum(item["complete_count"] for item in stats)
        open_count = sum(item["open_count"] for item in stats)
        valid = complete + open_count
        eligibility[horizon] = complete >= 2 and valid > 0 and complete / valid >= 0.5
    return eligibility


def _common_scoring_window(
    members: list[RebuiltPortfolioAnalysis],
    eligible_horizons: list[int],
) -> tuple[str, list[str]] | None:
    """One immutable scoring window shared by every candidate and member."""
    longest = max(eligible_horizons)
    population_dates = []
    for member in members:
        stats = next(item for item in member.signal_horizons if item["horizon"] == longest)
        completed = stats.get("completed_cohorts") or []
        if completed:
            population_dates.append(completed[0]["end_date"])
    baseline = max(population_dates, default=None)
    if baseline is None:
        return None
    all_dates = {
        point["date"]
        for member in members
        for point in member.policies[(longest, 100)].daily_returns
        if point["date"] > baseline
    }
    return baseline, sorted(all_dates)


def _member_returns_on_common_dates(
    member: RebuiltPortfolioAnalysis,
    horizon: int,
    exposure: int,
    dates: list[str],
    spy_by_date: dict[str, float],
) -> tuple[list[float], list[float]]:
    points = {point["date"]: point for point in member.policies[(horizon, exposure)].daily_returns}
    # An admitted member without an observation on a shared date contributes
    # SPY, exactly like a missing daily signal sleeve.
    strategy = [points[day]["return"] if day in points else spy_by_date[day] for day in dates]
    spy = [spy_by_date[day] for day in dates]
    return strategy, spy


def _compound_series(baseline: str, dates: list[str], returns: list[float]) -> Series:
    nav = 100.0
    series: Series = [{"date": baseline, "nav": nav}]
    for day, daily_return in zip(dates, returns, strict=True):
        nav *= 1.0 + daily_return
        series.append({"date": day, "nav": nav})
    return series


def _window_cost_drag(policy: PolicyResult, dates: list[str]) -> float:
    """Cost in base-100 percentage points over the selected return window."""
    daily_by_date = {point["date"]: point for point in policy.daily_returns}
    previous_nav: dict[str, float] = {}
    for previous, current in zip(policy.series, policy.series[1:], strict=False):
        previous_nav[current["date"]] = float(previous["nav"])
    drag = 0.0
    for day in dates:
        point = daily_by_date.get(day)
        if point is None:
            continue
        cost = float(point.get("cost") or 0.0)
        base = previous_nav.get(day)
        if base and base > 0:
            drag += cost / base * 100.0
    return drag


def _common_candidate_data(
    members: list[RebuiltPortfolioAnalysis],
    horizon: int,
    exposure: int,
    family_size: int,
    dates: list[str],
    baseline: str,
) -> _CommonCandidate | None:
    spy_by_date = {
        point["date"]: point["spy_return"]
        for member in members
        for point in member.policies[(horizon, exposure)].daily_returns
        if point["date"] in dates
    }
    usable_dates = [day for day in dates if day in spy_by_date]
    if not usable_dates:
        return None
    member_metrics: dict[int, dict] = {}
    member_series: dict[int, Series] = {}
    member_returns = []
    member_turnovers = []
    member_cost_drags = []
    for member in members:
        policy = member.policies[(horizon, exposure)]
        strategy, spy = _member_returns_on_common_dates(
            member,
            horizon,
            exposure,
            usable_dates,
            spy_by_date,
        )
        member_returns.append(strategy)
        turnover = sum(
            float(point.get("turnover_pct") or 0.0)
            for point in policy.daily_returns
            if point["date"] in usable_dates
        )
        cost_drag = _window_cost_drag(policy, usable_dates)
        member_turnovers.append(turnover)
        member_cost_drags.append(cost_drag)
        metrics = _daily_metrics(
            strategy,
            spy,
            usable_dates,
            baseline=baseline,
            horizon=horizon,
            family_size=family_size,
            turnover_pct=turnover,
            cost_drag_pct=cost_drag,
        )
        if not policy.metrics.get("eligible"):
            metrics.update(
                {
                    "eligible": False,
                    "ci_lower": None,
                    "ci_upper": None,
                    "evidence": "pending",
                }
            )
        member_metrics[member.portfolio.id] = metrics
        member_series[member.portfolio.id] = _compound_series(baseline, usable_dates, strategy)
    strategy_returns = [
        sum(returns[index] for returns in member_returns) / len(member_returns)
        for index in range(len(usable_dates))
    ]
    spy_returns = [spy_by_date[day] for day in usable_dates]
    metrics = _daily_metrics(
        strategy_returns,
        spy_returns,
        usable_dates,
        baseline=baseline,
        horizon=horizon,
        family_size=family_size,
        turnover_pct=sum(member_turnovers) / len(member_turnovers),
        cost_drag_pct=sum(member_cost_drags) / len(member_cost_drags),
    )
    metrics["portfolio_count"] = len(members)
    metrics["horizon"] = horizon
    metrics["exposure_pct"] = exposure
    return _CommonCandidate(
        metrics=metrics,
        member_metrics=member_metrics,
        member_series=member_series,
        meta_series=_compound_series(baseline, usable_dates, strategy_returns),
        spy_series=_compound_series(baseline, usable_dates, spy_returns),
    )


def _common_candidate_metrics(
    members: list[RebuiltPortfolioAnalysis],
    horizon: int,
    exposure: int,
    family_size: int,
    dates: list[str],
    baseline: str,
) -> tuple[dict, dict[int, dict]] | None:
    """Return Common meta and member metrics for one candidate policy."""
    if not dates:
        return None
    candidate = _common_candidate_data(
        members,
        horizon,
        exposure,
        family_size,
        dates,
        baseline,
    )
    if candidate is None:
        return None
    return candidate.metrics, candidate.member_metrics


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _select_common_policy(
    analyses: dict[int, RebuiltPortfolioAnalysis],
    objective: Objective,
) -> _CommonSelection:
    members = _rankable_common_members(analyses)
    selection = _CommonSelection(member_ids=_common_admitted_member_ids(analyses))
    if not members:
        return selection
    horizon_eligibility = _common_horizon_eligibility(members)
    eligible_horizons = [horizon for horizon, eligible in horizon_eligibility.items() if eligible]
    if not eligible_horizons:
        return selection
    window = _common_scoring_window(members, eligible_horizons)
    if window is None:
        return selection
    baseline, dates = window
    if not dates:
        return selection
    exposures = (100,) if objective == "canonical" else EXPOSURES
    eligible_pairs = [(horizon, exposure) for horizon in eligible_horizons for exposure in exposures]
    family_size = len(HORIZONS) if objective == "canonical" else len(HORIZONS) * len(EXPOSURES)
    candidates = [
        candidate
        for horizon, exposure in eligible_pairs
        if (
            candidate := _common_candidate_data(
                members,
                horizon,
                exposure,
                family_size,
                dates,
                baseline,
            )
        )
        is not None
    ]
    candidates = [
        candidate
        for candidate in candidates
        if selected_objective_score(candidate.metrics, objective) is not None
    ]
    if not candidates:
        return selection
    selected = max(
        candidates,
        key=lambda candidate: (
            selected_objective_score(candidate.metrics, objective),
            -candidate.metrics["exposure_pct"],
            -candidate.metrics["horizon"],
        ),
    )
    metrics = selected.metrics
    selection.policy = {
        "horizon": metrics["horizon"],
        "exposure_pct": metrics["exposure_pct"],
        "objective": objective,
        "scoring_start": baseline,
        "scoring_end": dates[-1],
        "metrics": metrics,
    }
    selection.member_metrics = selected.member_metrics
    selection.member_series = selected.member_series
    selection.spy_series = selected.spy_series
    selection.meta_series = selected.meta_series
    return selection


def _rebuilt_market_flags(
    portfolio: Portfolio,
    series: dict[str, Series],
    calendar: list[str],
    as_of: str | None,
    stale_symbols: set[str],
) -> tuple[bool, list[str]]:
    symbol_starts: dict[str, str] = {}
    for signal in portfolio.signals:
        effective = signal.effective_date.isoformat()
        for position in signal.positions:
            current = symbol_starts.get(position.symbol)
            if current is None or effective < current:
                symbol_starts[position.symbol] = effective
    if not symbol_starts:
        return False, []

    frozen: list[str] = []
    stale = False
    frozen_threshold = (
        calendar[-FROZEN_AFTER_TRADING_DAYS]
        if len(calendar) >= FROZEN_AFTER_TRADING_DAYS
        else (calendar[0] if calendar else None)
    )
    for symbol, effective in symbol_starts.items():
        available = {
            str(point["date"])
            for point in series.get(symbol, [])
            if point.get("close") is not None and (as_of is None or str(point["date"]) <= as_of)
        }
        required = [day for day in calendar if day >= effective]
        last_print = max(available, default=None)
        if last_print is None or symbol in stale_symbols or any(day not in available for day in required):
            stale = True
        if last_print is None or (frozen_threshold is not None and last_print < frozen_threshold):
            frozen.append(symbol)
    return stale, sorted(frozen)


def compute_rebuilt_arena(
    session: Session,
    portfolios: list[Portfolio],
    *,
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    now: datetime | None = None,
) -> RebuiltArena:
    """Load prices once and evaluate every rebuilt portfolio deterministically."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    rebuilt = [portfolio for portfolio in portfolios if portfolio.prompt_mode == "rebuilt"]
    signals = [signal for portfolio in rebuilt for signal in portfolio.signals]
    requirements = signal_pricing_requirements(
        signals,
        fallback_start=now.astimezone(NY).date() - timedelta(days=45),
    )
    price_load = load_price_series(session, requirements, now)
    spy_series = price_load.series.get(SPY_SYMBOL) or []
    as_of = _as_of(spy_series, price_cache.latest_available_session(now))
    calendar = build_calendar(spy_series, as_of) if as_of is not None else []
    arena = RebuiltArena(
        as_of=as_of,
        market_data_status=price_load.status,
        spy_series=spy_series,
        calendar=calendar,
        objective=objective,
        cost_basis=cost_basis,
    )
    for portfolio in rebuilt:
        stale_data, frozen_symbols = _rebuilt_market_flags(
            portfolio,
            price_load.series,
            calendar,
            as_of,
            price_load.stale_symbols,
        )
        try:
            horizon_stats, policies, selected = evaluate_policy_grid(
                _signal_inputs(portfolio.signals),
                price_load.series,
                calendar,
                portfolio.cost_bps,
                cost_basis,
                objective,
            )
            policy_map = {(item.horizon, item.exposure_pct): item for item in policies}
            arena.by_portfolio_id[portfolio.id] = RebuiltPortfolioAnalysis(
                portfolio=portfolio,
                signal_horizons=horizon_stats,
                policies=policy_map,
                selected=selected,
                stale_data=stale_data,
                frozen_symbols=frozen_symbols,
            )
        except (ValueError, RebuiltValuationError) as exc:
            logger.warning("cannot evaluate rebuilt portfolio %s: %s", portfolio.slug, exc)
            arena.by_portfolio_id[portfolio.id] = RebuiltPortfolioAnalysis(
                portfolio=portfolio,
                signal_horizons=[],
                policies={},
                selected=None,
                error=str(exc),
                stale_data=stale_data,
                frozen_symbols=frozen_symbols,
            )
            arena.market_data_status = "unavailable"
        if stale_data and arena.market_data_status == "fresh":
            arena.market_data_status = "stale"
    common = _select_common_policy(
        arena.by_portfolio_id,
        objective,
    )
    arena.common_policy = common.policy
    arena.common_member_ids = common.member_ids
    arena.common_metrics_by_portfolio_id = common.member_metrics
    arena.common_series_by_portfolio_id = common.member_series
    arena.common_spy_series = common.spy_series
    arena.common_meta_series = common.meta_series
    return arena
