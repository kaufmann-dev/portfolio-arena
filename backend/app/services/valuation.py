"""Pure, transaction-cost-free valuation on the shared open/close timeline.

Every timestamp comes from caller-supplied prices and boundaries. Holdings only
change at decision boundaries; extra chart observations never introduce trades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from .trading_calendar import boundary_value, is_trading_day

TRADING_DAYS_PER_YEAR = 252
FROZEN_AFTER_TRADING_DAYS = 5
Series = list[dict]
Direction = Literal["long", "short"]
Boundary = dict[str, str]
Phase = Literal["open", "close"]


def point_boundary(point: dict) -> Boundary:
    return {"timestamp": point["timestamp"], "phase": point["phase"]}


def boundary_date(point: Boundary) -> str:
    return point["timestamp"][:10]


@dataclass(frozen=True)
class PositionInput:
    symbol: str
    weight_pct: float
    note: str = ""


@dataclass(frozen=True)
class AllocationInput:
    effective_date: str
    positions: tuple[PositionInput, ...]


@dataclass
class AppliedAllocation:
    effective_date: str
    effective_at: Boundary
    applied_at: Boundary | None
    turnover_pct: float | None
    nav_before: float | None
    nav_after: float | None


@dataclass
class Holding:
    symbol: str
    weight_pct: float
    target_weight_pct: float
    value: float
    entry_price: float | None = None
    current_price: float | None = None
    note: str = ""


@dataclass
class ValuationResult:
    series: Series
    allocations: list[AppliedAllocation]
    holdings: list[Holding]
    stale_days: dict[str, list[str]] = field(default_factory=dict)
    frozen_symbols: list[str] = field(default_factory=list)
    cumulative_turnover_pct: float = 0.0
    liquidated_at: Boundary | None = None


class ValuationError(ValueError):
    """The supplied market observations cannot price this portfolio."""


class PriceLookup:
    """Exact boundary prices: an opening print is never replaced by a close."""

    def __init__(self, points: Series):
        self.values = {
            (str(point["date"]), phase): float(point[phase])
            for point in points
            for phase in ("open", "close")
            if point.get(phase) is not None and math.isfinite(float(point[phase])) and float(point[phase]) > 0
        }

    def at(self, boundary: Boundary) -> float | None:
        return self.values.get((boundary_date(boundary), boundary["phase"]))

    def require(self, boundary: Boundary, symbol: str) -> float:
        value = self.at(boundary)
        if value is None:
            raise ValuationError(
                f"Missing {boundary['phase']} price for {symbol} at {boundary['timestamp']}."
            )
        return value


def build_calendar(spy_series: Series, as_of: Boundary) -> list[Boundary]:
    """Every scheduled boundary in the observed range, including missing prints.

    A missing full SPY session must not compress holding horizons or make a
    multiple-session price change appear to be one full-session observation.
    """
    if not spy_series:
        return []
    day = date.fromisoformat(min(point["date"] for point in spy_series))
    last = date.fromisoformat(boundary_date(as_of))
    calendar = []
    while day <= last:
        if is_trading_day(day):
            for phase in ("open", "close"):
                boundary = boundary_value(day, phase)
                if boundary["timestamp"] <= as_of["timestamp"]:
                    calendar.append(boundary)
        day += timedelta(days=1)
    return calendar


def value_portfolio(
    allocations: list[AllocationInput],
    prices: dict[str, Series],
    calendar: list[Boundary],
    as_of: Boundary,
    direction: Direction = "long",
    execution_boundary: Phase = "close",
) -> ValuationResult:
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    if execution_boundary not in ("open", "close"):
        raise ValueError("execution_boundary must be open or close")
    events = [event for event in calendar if event["timestamp"] <= as_of["timestamp"]]
    lookups = {symbol: PriceLookup(points) for symbol, points in prices.items()}
    schedule: dict[str, list[AllocationInput]] = {}
    applied: list[AppliedAllocation] = []
    for allocation in sorted(allocations, key=lambda item: item.effective_date):
        event = next(
            (
                item
                for item in events
                if item["phase"] == execution_boundary and boundary_date(item) >= allocation.effective_date
            ),
            None,
        )
        if event is None:
            applied.append(
                AppliedAllocation(
                    allocation.effective_date,
                    boundary_value(date.fromisoformat(allocation.effective_date), execution_boundary),
                    None,
                    None,
                    None,
                    None,
                )
            )
        else:
            schedule.setdefault(event["timestamp"], []).append(allocation)
    if not schedule:
        return ValuationResult([], applied, [])

    quantities: dict[str, float] = {}
    entries: dict[str, float] = {}
    targets: dict[str, PositionInput] = {}
    anchor = 100.0
    turnover_total = 0.0
    series: Series = []
    liquidated_at = None

    def price(symbol: str, event: Boundary) -> float:
        if symbol not in lookups:
            raise ValuationError(f"Missing price series for {symbol}.")
        return lookups[symbol].require(event, symbol)

    def equity(event: Boundary) -> float:
        if direction == "long":
            return sum(quantity * price(symbol, event) for symbol, quantity in quantities.items())
        return anchor + sum(
            quantity * (entries[symbol] - price(symbol, event)) for symbol, quantity in quantities.items()
        )

    first = min(schedule)
    for event in events:
        if event["timestamp"] < first:
            continue
        nav = equity(event) if series else 100.0
        if liquidated_at is None and series and nav <= 0:
            liquidated_at = event
            quantities = {}
            targets = {}
            anchor = 0.0
        if liquidated_at is None:
            for allocation in schedule.get(event["timestamp"], []):
                positions = [position for position in allocation.positions if position.weight_pct > 0]
                weights = {
                    symbol: quantity * price(symbol, event) / nav * 100
                    for symbol, quantity in quantities.items()
                }
                new_weights = {position.symbol: position.weight_pct for position in positions}
                turnover = (
                    0.5
                    * sum(
                        abs(weights.get(symbol, 0) - new_weights.get(symbol, 0))
                        for symbol in weights.keys() | new_weights.keys()
                    )
                    if series or quantities
                    else None
                )
                turnover_total += turnover or 0.0
                entries = {position.symbol: price(position.symbol, event) for position in positions}
                quantities = {
                    position.symbol: nav * position.weight_pct / 100 / entries[position.symbol]
                    for position in positions
                }
                targets = {position.symbol: position for position in positions}
                anchor = nav
                applied.append(
                    AppliedAllocation(
                        allocation.effective_date,
                        boundary_value(date.fromisoformat(allocation.effective_date), execution_boundary),
                        event,
                        turnover,
                        nav,
                        nav,
                    )
                )
        else:
            for allocation in schedule.get(event["timestamp"], []):
                applied.append(
                    AppliedAllocation(
                        allocation.effective_date,
                        boundary_value(date.fromisoformat(allocation.effective_date), execution_boundary),
                        None,
                        None,
                        None,
                        None,
                    )
                )
        series.append({**event, "nav": max(0.0, nav) if liquidated_at is None else 0.0})

    holdings = []
    if series and liquidated_at is None and series[-1]["nav"] > 0:
        event = point_boundary(series[-1])
        for symbol, quantity in sorted(quantities.items()):
            current = price(symbol, event)
            holdings.append(
                Holding(
                    symbol,
                    quantity * current / series[-1]["nav"] * 100,
                    targets[symbol].weight_pct,
                    quantity * current,
                    entries[symbol],
                    current,
                    targets[symbol].note,
                )
            )
    return ValuationResult(
        series,
        sorted(applied, key=lambda item: item.effective_date),
        holdings,
        cumulative_turnover_pct=turnover_total,
        liquidated_at=liquidated_at,
    )


def rebase_series(points: Series, start: Boundary, end: Boundary, direction: Direction = "long") -> Series:
    """Direction-matched SPY; short reference resets only at market close."""
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    calendar = [event for event in build_calendar(points, end) if event["timestamp"] >= start["timestamp"]]
    if not calendar:
        return []
    lookup = PriceLookup(points)
    entry = lookup.require(calendar[0], "SPY")
    anchor = 100.0
    result = []
    liquidated = False
    for event in calendar:
        current = lookup.require(event, "SPY")
        nav = current / entry * 100 if direction == "long" else anchor * (2 - current / entry)
        if liquidated or nav <= 0:
            nav = 0.0
            liquidated = True
        result.append({**event, "nav": nav})
        if direction == "short" and event["phase"] == "close":
            anchor, entry = nav, current
    return result


def full_session_points(series: Series) -> Series:
    """Nonoverlapping full sessions ending at the latest published phase."""
    if not series:
        return []
    phase = series[-1]["phase"]
    return [point for point in series if point["phase"] == phase]


def session_returns(series: Series, benchmark: Series) -> list[dict]:
    points = full_session_points(series)
    benchmark_values = {point["timestamp"]: point["nav"] for point in benchmark}
    returns = []
    for previous, current in zip(points, points[1:], strict=False):
        if previous["nav"] <= 0:
            continue
        prior = benchmark_values.get(previous["timestamp"])
        latest = benchmark_values.get(current["timestamp"])
        market_return = latest / prior - 1 if prior is not None and prior > 0 and latest is not None else None
        strategy_return = current["nav"] / previous["nav"] - 1
        returns.append(
            {
                **point_boundary(current),
                "return": strategy_return,
                "spy_return": market_return,
                "alpha": strategy_return - market_return if market_return is not None else None,
            }
        )
    return returns


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def series_metrics(
    series: Series, benchmark: Series, turnover: float, liquidated_at: Boundary | None
) -> dict:
    if not series:
        return {"has_data": False}
    daily = session_returns(series, benchmark)
    returns = [point["return"] for point in daily]
    alphas = [point["alpha"] for point in daily if point["alpha"] is not None]
    std = sample_std(returns)
    alpha_std = sample_std(alphas)
    peak = 100.0
    drawdown = 0.0
    for point in series:
        peak = max(peak, point["nav"])
        if peak > 0:
            drawdown = min(drawdown, point["nav"] / peak - 1)
    total_return = series[-1]["nav"] / 100 - 1
    spy_return = benchmark[-1]["nav"] / 100 - 1 if benchmark else None
    return {
        "has_data": True,
        "start_at": point_boundary(series[0]),
        "end_at": point_boundary(series[-1]),
        "itd_return": total_return,
        "spy_return": spy_return,
        "cumulative_excess": total_return - spy_return if spy_return is not None else None,
        "ann_volatility": std * math.sqrt(TRADING_DAYS_PER_YEAR) if returns else None,
        "sharpe": sum(returns) / len(returns) / std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if returns and std > 0
        else None,
        "information_ratio": sum(alphas) / len(alphas) / alpha_std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if alphas and alpha_std > 0
        else None,
        "max_drawdown": drawdown,
        "turnover_pct": turnover,
        "liquidated_at": liquidated_at,
    }


def compute_metrics(result: ValuationResult, spy_series: Series, direction: Direction = "long") -> dict:
    from .rebuilt import automatic_hac_lag, hac_mean_statistics

    if not result.series:
        return {"has_data": False}
    series = result.series
    benchmark = rebase_series(spy_series, point_boundary(series[0]), point_boundary(series[-1]), direction)
    alphas = [point["alpha"] for point in session_returns(series, benchmark) if point["alpha"] is not None]
    metrics = series_metrics(series, benchmark, result.cumulative_turnover_pct, result.liquidated_at)
    metrics.update(hac_mean_statistics(alphas, automatic_hac_lag(len(alphas))))
    for name, days in {"r1m": 30, "r3m": 91, "r6m": 182, "r1y": 365}.items():
        anchor_day = (date.fromisoformat(boundary_date(series[-1])) - timedelta(days=days)).isoformat()
        bases = [point["nav"] for point in series if boundary_date(point) <= anchor_day]
        metrics[name] = series[-1]["nav"] / bases[-1] - 1 if bases and bases[-1] > 0 else None
    return metrics
