"""Pure rebuilt analytics: forty half-session horizons, one fixed-exposure policy.

Signals enter once per session. A horizon H uses 1/ceil(H) sleeves; unused
capacity follows direction-matched SPY. Only entries/expiries change holdings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property
from statistics import NormalDist, median
from typing import Literal, get_args

from .trading_calendar import boundary_value, is_trading_day
from .valuation import (
    Boundary,
    Direction,
    Phase,
    PositionInput,
    PreparedReference,
    PriceLookup,
    Series,
    ValuationError,
    boundary_date,
    series_metrics,
    session_returns,
)

HORIZONS = tuple(step / 2 for step in range(1, 41))
DIRECT_SEARCH_FAMILY_SIZE = len(HORIZONS)
Evidence = Literal["pending", "inconclusive", "positive", "negative"]
HorizonObjective = Literal[
    "signal_mean_daily_alpha", "ci_lower", "information_ratio", "sharpe", "mean_daily_alpha", "hit_rate"
]


@dataclass(frozen=True)
class SignalInput:
    id: int
    effective_date: str
    positions: tuple[PositionInput, ...]


@dataclass(frozen=True)
class MappedSignal:
    signal: SignalInput
    start_index: int


@dataclass
class PolicyResult:
    horizon: float
    series: Series
    spy_series: Series
    daily_returns: list[dict]
    holdings: list[dict]
    active_cohorts: list[dict]
    cumulative_turnover_pct: float
    direction: Direction = "long"
    liquidated_at: Boundary | None = None
    metrics: dict = field(default_factory=dict)
    reference_holding: dict | None = None


class RebuiltValuationError(ValuationError):
    """A rebuilt cohort cannot be priced from the supplied observations."""


@dataclass
class PreparedMarket:
    prices: dict[str, Series]
    calendar: list[Boundary]
    lookups: dict[str, PriceLookup]

    @cached_property
    def reference(self) -> PreparedReference:
        return PreparedReference(self.prices["SPY"], self.calendar[-1], lookup=self.lookups["SPY"])

    def price(self, symbol: str, event: Boundary) -> float:
        lookup = self.lookups.get(symbol)
        if lookup is None:
            raise RebuiltValuationError(f"Missing price series for {symbol}.")
        return lookup.require(event, symbol)


def prepare_market(prices: dict[str, Series], calendar: list[Boundary]) -> PreparedMarket:
    return PreparedMarket(
        prices, calendar, {symbol: PriceLookup(points) for symbol, points in prices.items()}
    )


def map_signals(
    signals: list[SignalInput], calendar: list[Boundary], execution_boundary: Phase = "close"
) -> list[MappedSignal]:
    mapped = []
    for signal in sorted(signals, key=lambda item: (item.effective_date, item.id)):
        index = next(
            (
                index
                for index, event in enumerate(calendar)
                if event["phase"] == execution_boundary and boundary_date(event) >= signal.effective_date
            ),
            None,
        )
        if index is not None:
            mapped.append(MappedSignal(signal, index))
    return mapped


def _evidence(lower: float | None, upper: float | None) -> Evidence:
    if lower is None or upper is None:
        return "pending"
    if lower > 0:
        return "positive"
    if upper < 0:
        return "negative"
    return "inconclusive"


def hac_mean_statistics(values: list[float], lag: int, family_size: int = 1) -> dict:
    """Mean and two-sided Bonferroni family-wise 95% Newey-West interval.

    ``lag`` is capped to the available sample.  The Bartlett-kernel long-run
    variance is divided by ``n`` to obtain the variance of the sample mean.
    """
    finite = [float(value) for value in values if math.isfinite(float(value))]
    count = len(finite)
    if not count:
        return {
            "observation_count": 0,
            "mean_daily_alpha": None,
            "median_daily_alpha": None,
            "hit_rate": None,
            "hac_lag": 0,
            "hac_standard_error": None,
            "family_size": max(1, family_size),
            "ci_lower": None,
            "ci_upper": None,
            "evidence": "pending",
        }

    mean = sum(finite) / count
    if count < 2:
        return {
            "observation_count": count,
            "mean_daily_alpha": mean,
            "median_daily_alpha": median(finite),
            "hit_rate": sum(value > 0 for value in finite) / count,
            "hac_lag": 0,
            "hac_standard_error": None,
            "family_size": max(1, family_size),
            "ci_lower": None,
            "ci_upper": None,
            "evidence": "pending",
        }
    actual_lag = min(max(0, lag), count - 1)
    centered = [value - mean for value in finite]
    gamma_zero = sum(value * value for value in centered) / count
    long_run_variance = gamma_zero
    for offset in range(1, actual_lag + 1):
        covariance = sum(centered[index] * centered[index - offset] for index in range(offset, count)) / count
        long_run_variance += 2.0 * (1.0 - offset / (actual_lag + 1.0)) * covariance
    standard_error = math.sqrt(max(0.0, long_run_variance) / count)

    comparisons = max(1, family_size)
    critical = NormalDist().inv_cdf(1.0 - 0.05 / (2.0 * comparisons))
    lower = mean - critical * standard_error
    upper = mean + critical * standard_error
    return {
        "observation_count": count,
        "mean_daily_alpha": mean,
        "median_daily_alpha": median(finite),
        "hit_rate": sum(value > 0 for value in finite) / count,
        "hac_lag": actual_lag,
        "hac_standard_error": standard_error,
        "family_size": comparisons,
        "ci_lower": lower,
        "ci_upper": upper,
        "evidence": _evidence(lower, upper),
    }


def automatic_hac_lag(observation_count: int) -> int:
    """A small-sample-bounded Newey-West bandwidth for managed portfolios."""
    if observation_count < 2:
        return 0
    proposed = math.floor(4.0 * (observation_count / 100.0) ** (2.0 / 9.0))
    return min(observation_count - 1, max(1, proposed))


def signal_horizon_statistics(
    signals: list[SignalInput],
    prices: dict[str, Series],
    calendar: list[Boundary],
    horizon: float,
    family_size: int = DIRECT_SEARCH_FAMILY_SIZE,
    direction: Direction = "long",
    execution_boundary: Phase = "close",
    *,
    prepared_market: PreparedMarket | None = None,
) -> dict:
    """Direct basket evidence, normalized to one full trading session."""
    if horizon not in HORIZONS:
        raise ValueError("horizon must be H0.5 through H20 in half steps")
    market = prepared_market or prepare_market(prices, calendar)
    mapped = map_signals(signals, calendar, execution_boundary)
    completed = []
    values = []
    open_count = len(signals) - len(mapped)
    invalid_count = 0
    steps = int(horizon * 2)
    for item in mapped:
        planned_end = item.start_index + steps
        final_index = min(planned_end, len(calendar) - 1)
        start = calendar[item.start_index]
        entries = {}
        try:
            entries = {
                position.symbol: market.price(position.symbol, start)
                for position in item.signal.positions
                if position.weight_pct > 0
            }
            liquidation_index = None
            basket_return = 0.0
            reference_weight = max(
                0.0, 1 - sum(position.weight_pct for position in item.signal.positions) / 100
            )
            reference_series = market.reference.rebase(start, calendar[final_index], direction)
            reference_returns = {point["timestamp"]: point["nav"] / 100 - 1 for point in reference_series}
            for index in range(item.start_index + 1, final_index + 1):
                underlying = sum(
                    position.weight_pct
                    / 100
                    * (market.price(position.symbol, calendar[index]) / entries[position.symbol] - 1)
                    for position in item.signal.positions
                    if position.weight_pct > 0
                )
                basket_return = underlying if direction == "long" else -underlying
                basket_return += reference_weight * reference_returns[calendar[index]["timestamp"]]
                if basket_return <= -1:
                    basket_return = -1.0
                    liquidation_index = index
                    break
            if planned_end >= len(calendar) and liquidation_index is None:
                open_count += 1
                continue
            end_index = liquidation_index if liquidation_index is not None else planned_end
            end = calendar[end_index]
            benchmark_return = reference_returns[end["timestamp"]]
            if benchmark_return <= -1:
                invalid_count += 1
                continue
            observed_sessions = (end_index - item.start_index) / 2
            daily_alpha = ((1 + basket_return) / (1 + benchmark_return)) ** (1 / observed_sessions) - 1
            values.append(daily_alpha)
            completed.append(
                {
                    "signal_id": item.signal.id,
                    "start_at": start,
                    "end_at": end,
                    "signal_return": basket_return,
                    "spy_return": benchmark_return,
                    "daily_alpha": daily_alpha,
                    "liquidated_at": end if liquidation_index is not None else None,
                }
            )
        except (ValuationError, KeyError):
            invalid_count += 1
    denominator = len(values) + open_count
    completion_ratio = len(values) / denominator if denominator else 0.0
    eligible = len(values) >= 2 and completion_ratio >= 0.5 and invalid_count == 0
    result = {
        "horizon": horizon,
        "complete_count": len(values),
        "open_count": open_count,
        "invalid_count": invalid_count,
        "completion_ratio": completion_ratio,
        "eligible": eligible,
        "completed_cohorts": completed,
        **hac_mean_statistics(values, lag=math.ceil(horizon) - 1, family_size=family_size),
    }
    if not eligible:
        result.update(ci_lower=None, ci_upper=None, evidence="pending")
    return result


def _targets(
    mapped: list[MappedSignal], index: int, steps: int, horizon: float
) -> tuple[dict[str, float], list[MappedSignal]]:
    active = [item for item in mapped if item.start_index <= index < item.start_index + steps]
    groups: dict[int, list[MappedSignal]] = {}
    for item in active:
        groups.setdefault(item.start_index, []).append(item)
    target: dict[str, float] = {}
    sleeve = 1 / math.ceil(horizon)
    for group in groups.values():
        for item in group:
            for position in item.signal.positions:
                if position.weight_pct > 0:
                    target[position.symbol] = (
                        target.get(position.symbol, 0.0) + sleeve / len(group) * position.weight_pct / 100
                    )
    # Distinct from a SPY stock recommendation: this is the daily-reset reference.
    target["__reference__"] = max(0.0, 1 - sum(target.values()))
    return target, active


def _cohort_end(calendar: list[Boundary], end_index: int) -> Boundary:
    if end_index < len(calendar):
        return calendar[end_index]
    day = date.fromisoformat(boundary_date(calendar[-1]))
    phase = calendar[-1]["phase"]
    for _ in range(end_index - len(calendar) + 1):
        if phase == "open":
            phase = "close"
        else:
            phase = "open"
            day += timedelta(days=1)
            while not is_trading_day(day):
                day += timedelta(days=1)
    return boundary_value(day, phase)


def construct_policy(
    signals: list[SignalInput],
    prices: dict[str, Series],
    calendar: list[Boundary],
    horizon: float,
    direction: Direction = "long",
    execution_boundary: Phase = "close",
    *,
    prepared_market: PreparedMarket | None = None,
) -> PolicyResult:
    if horizon not in HORIZONS:
        raise ValueError("horizon must be H0.5 through H20 in half steps")
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    market = prepared_market or prepare_market(prices, calendar)
    mapped = map_signals(signals, calendar, execution_boundary)
    if not mapped:
        return PolicyResult(horizon, [], [], [], [], [], 0.0, direction)
    steps = int(horizon * 2)
    first_index = min(item.start_index for item in mapped)
    benchmark = market.reference.rebase(calendar[first_index], calendar[-1], direction)
    reference = {point["timestamp"]: point["nav"] for point in benchmark}
    trade_indices = {item.start_index for item in mapped} | {item.start_index + steps for item in mapped}
    quantities: dict[str, float] = {}
    entries: dict[str, float] = {}
    collateral = 0.0
    reference_units = 0.0
    series = []
    turnover_total = 0.0
    liquidated_at = None
    active: list[MappedSignal] = []
    target: dict[str, float] = {}

    def current_values(event: Boundary) -> dict[str, float]:
        values = {symbol: quantity * market.price(symbol, event) for symbol, quantity in quantities.items()}
        values["__reference__"] = reference_units * reference[event["timestamp"]]
        return values

    for index in range(first_index, len(calendar)):
        event = calendar[index]
        if liquidated_at is not None:
            series.append({**event, "nav": 0.0})
            continue
        values = current_values(event)
        stock_value = sum(value for symbol, value in values.items() if symbol != "__reference__")
        nav = (
            (
                stock_value
                if direction == "long"
                else collateral
                + sum(
                    quantity * (entries[symbol] - market.price(symbol, event))
                    for symbol, quantity in quantities.items()
                )
            )
            + values["__reference__"]
            if series
            else 100.0
        )
        if nav <= 0:
            liquidated_at = event
            series.append({**event, "nav": 0.0})
            quantities, active, target = {}, [], {}
            continue
        if index in trade_indices:
            target, active = _targets(mapped, index, steps, horizon)
            weights = (
                {symbol: value / nav for symbol, value in values.items()}
                if series
                else {"__reference__": 1.0}
            )
            turnover_target = dict(target)
            if direction == "long":
                # A long SPY recommendation and the unused reference sleeve
                # own the same asset; moving between them is not a trade.
                for basket in (weights, turnover_target):
                    basket["SPY"] = basket.get("SPY", 0.0) + basket.pop("__reference__", 0.0)
            turnover_total += (
                0.5
                * sum(
                    abs(turnover_target.get(symbol, 0) - weights.get(symbol, 0))
                    for symbol in turnover_target.keys() | weights.keys()
                )
                * 100
            )
            entries = {
                symbol: market.price(symbol, event)
                for symbol, weight in target.items()
                if symbol != "__reference__" and weight > 0
            }
            quantities = {symbol: nav * target[symbol] / entry for symbol, entry in entries.items()}
            collateral = nav * sum(weight for symbol, weight in target.items() if symbol != "__reference__")
            reference_value = reference[event["timestamp"]]
            if target.get("__reference__", 0) and reference_value <= 0:
                raise RebuiltValuationError("Direction-matched SPY reference has liquidated.")
            reference_units = (
                nav * target.get("__reference__", 0) / reference_value if reference_value > 0 else 0.0
            )
        series.append({**event, "nav": nav})

    holdings = []
    reference_holding = None
    if liquidated_at is None:
        final_values = current_values(calendar[-1])
        merged: dict[str, float] = {}
        for symbol, value in final_values.items():
            if symbol == "__reference__":
                if value > 0:
                    reference_holding = {"weight_pct": value / series[-1]["nav"] * 100}
            else:
                merged[symbol] = merged.get(symbol, 0.0) + value
        holdings = [
            {"symbol": symbol, "weight_pct": value / series[-1]["nav"] * 100}
            for symbol, value in sorted(merged.items())
            if value > 0
        ]
    active_out = (
        [
            {
                "signal_id": item.signal.id,
                "start_at": calendar[item.start_index],
                "end_at": _cohort_end(calendar, item.start_index + steps),
                "age_sessions": (len(calendar) - 1 - item.start_index) / 2,
                "reference_weight_pct": max(0.0, 100 - sum(p.weight_pct for p in item.signal.positions)),
                "positions": [
                    {"symbol": position.symbol, "weight_pct": position.weight_pct}
                    for position in item.signal.positions
                    if position.weight_pct > 0
                ],
            }
            for item in active
        ]
        if liquidated_at is None
        else []
    )
    return PolicyResult(
        horizon,
        series,
        benchmark,
        session_returns(series, benchmark),
        holdings,
        active_out,
        turnover_total,
        direction,
        liquidated_at,
        reference_holding=reference_holding,
    )


def policy_metrics(
    result: PolicyResult, completion: dict, family_size: int = DIRECT_SEARCH_FAMILY_SIZE
) -> dict:
    metrics = series_metrics(
        result.series, result.spy_series, result.cumulative_turnover_pct, result.liquidated_at
    )
    alphas = [point["alpha"] for point in result.daily_returns if point["alpha"] is not None]
    metrics.update(hac_mean_statistics(alphas, lag=math.ceil(result.horizon) - 1, family_size=family_size))
    metrics["signal_mean_daily_alpha"] = completion["mean_daily_alpha"]
    metrics.update(
        {key: completion[key] for key in ("complete_count", "open_count", "completion_ratio", "eligible")}
    )
    if not completion["eligible"]:
        metrics.update(ci_lower=None, ci_upper=None, evidence="pending")
    return metrics


def select_policy(
    candidates: list[PolicyResult], objective: HorizonObjective = "signal_mean_daily_alpha"
) -> PolicyResult | None:
    if objective not in get_args(HorizonObjective):
        raise ValueError("Unknown horizon optimization objective")
    eligible = [
        candidate
        for candidate in candidates
        if candidate.metrics.get("eligible")
        and candidate.metrics.get("ci_lower") is not None
        and (score := candidate.metrics.get(objective)) is not None
        and math.isfinite(score)
    ]
    return max(
        eligible, key=lambda candidate: (candidate.metrics[objective], -candidate.horizon), default=None
    )


def evaluate_policy_grid(
    signals: list[SignalInput],
    prices: dict[str, Series],
    calendar: list[Boundary],
    direction: Direction = "long",
    execution_boundary: Phase = "close",
    *,
    prepared_market: PreparedMarket | None = None,
) -> tuple[list[dict], list[PolicyResult]]:
    market = prepared_market or prepare_market(prices, calendar)
    statistics = [
        signal_horizon_statistics(
            signals,
            prices,
            calendar,
            horizon,
            direction=direction,
            execution_boundary=execution_boundary,
            prepared_market=market,
        )
        for horizon in HORIZONS
    ]
    policies = []
    for horizon, completion in zip(HORIZONS, statistics, strict=True):
        policy = construct_policy(
            signals, prices, calendar, horizon, direction, execution_boundary, prepared_market=market
        )
        policy.metrics = policy_metrics(policy, completion)
        policies.append(policy)
    return statistics, policies
