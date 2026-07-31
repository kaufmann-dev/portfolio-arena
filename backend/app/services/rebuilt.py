"""Pure analytics for daily, independent rebuilt-portfolio signals.

The engine has no database or wall-clock dependencies.  A signal starts at its
effective close and remains in a cohort for exactly ``horizon`` close-to-close
intervals.  Each active cohort receives ``exposure / horizon`` percent of the
aggregate portfolio; every unused sleeve remains invested in SPY.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from statistics import NormalDist, median
from typing import Literal

from .valuation import TRADING_DAYS_PER_YEAR, PositionInput, Series

HORIZONS = tuple(range(1, 21))
EXPOSURES = tuple(range(10, 101, 10))
DIRECT_SEARCH_FAMILY_SIZE = len(HORIZONS)
OPTIMIZED_SEARCH_FAMILY_SIZE = len(HORIZONS) * len(EXPOSURES)
CostBasis = Literal["gross", "net"]
Objective = Literal["canonical", "max_alpha", "max_information_ratio", "max_sharpe"]
Evidence = Literal["pending", "inconclusive", "positive", "negative"]


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
    horizon: int
    exposure_pct: int
    cost_basis: CostBasis
    series: Series
    spy_series: Series
    daily_returns: list[dict]
    holdings: list[dict]
    active_cohorts: list[dict]
    cumulative_cost: float
    cumulative_turnover_pct: float
    metrics: dict = field(default_factory=dict)


class RebuiltValuationError(ValueError):
    """A rebuilt cohort cannot be valued with the supplied series."""


class _Lookup:
    def __init__(self, points: Series):
        cleaned = sorted(
            (str(point["date"]), float(point["close"]))
            for point in points or []
            if point.get("close") is not None
            and math.isfinite(float(point["close"]))
            and float(point["close"]) > 0
        )
        self.dates = [day for day, _ in cleaned]
        self.values = [value for _, value in cleaned]

    def at(self, day: str) -> float | None:
        index = bisect_right(self.dates, day)
        return self.values[index - 1] if index else None


def map_signals(signals: list[SignalInput], calendar: list[str]) -> list[MappedSignal]:
    """Map effective dates to the first actual SPY session on or after them."""
    mapped: list[MappedSignal] = []
    for signal in sorted(signals, key=lambda item: (item.effective_date, item.id)):
        index = bisect_left(calendar, signal.effective_date)
        if index < len(calendar):
            mapped.append(MappedSignal(signal=signal, start_index=index))
    return mapped


def _weighted_basket_return(
    signal: SignalInput,
    start_day: str,
    end_day: str,
    lookups: dict[str, _Lookup],
) -> float | None:
    result = 0.0
    for position in signal.positions:
        if position.weight_pct <= 0:
            continue
        lookup = lookups.get(position.symbol)
        start = lookup.at(start_day) if lookup else None
        end = lookup.at(end_day) if lookup else None
        if start is None or end is None:
            return None
        result += position.weight_pct / 100.0 * (end / start - 1.0)
    return result


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
    calendar: list[str],
    horizon: int,
    family_size: int = DIRECT_SEARCH_FAMILY_SIZE,
) -> dict:
    """Direct, completed-cohort signal alpha for one holding horizon."""
    if horizon not in HORIZONS:
        raise ValueError("horizon must be between 1 and 20")
    lookups = {symbol: _Lookup(points) for symbol, points in prices.items()}
    spy = lookups.get("SPY")
    if spy is None:
        raise RebuiltValuationError("SPY price series is required")

    completed_values: list[float] = []
    completed: list[dict] = []
    mapped_signals = map_signals(signals, calendar)
    # A signal after the last priced session has begun its lifecycle but has
    # no observable entry close yet. It is open evidence, not invalid evidence.
    open_count = len(signals) - len(mapped_signals)
    invalid_count = 0
    for mapped in mapped_signals:
        end_index = mapped.start_index + horizon
        start_day = calendar[mapped.start_index]
        if end_index >= len(calendar):
            open_count += 1
            continue
        end_day = calendar[end_index]
        basket_return = _weighted_basket_return(mapped.signal, start_day, end_day, lookups)
        spy_start = spy.at(start_day)
        spy_end = spy.at(end_day)
        if basket_return is None or spy_start is None or spy_end is None:
            invalid_count += 1
            continue
        spy_return = spy_end / spy_start - 1.0
        if 1.0 + basket_return <= 0 or 1.0 + spy_return <= 0:
            invalid_count += 1
            continue
        daily_alpha = ((1.0 + basket_return) / (1.0 + spy_return)) ** (1.0 / horizon) - 1.0
        completed_values.append(daily_alpha)
        completed.append(
            {
                "signal_id": mapped.signal.id,
                "start_date": start_day,
                "end_date": end_day,
                "signal_return": basket_return,
                "spy_return": spy_return,
                "daily_alpha": daily_alpha,
            }
        )

    valid_count = len(completed_values) + open_count
    completion_ratio = len(completed_values) / valid_count if valid_count else 0.0
    eligible = len(completed_values) >= 2 and completion_ratio >= 0.5
    result = {
        "horizon": horizon,
        "complete_count": len(completed_values),
        "open_count": open_count,
        "invalid_count": invalid_count,
        "completion_ratio": completion_ratio,
        "eligible": eligible,
        "completed_cohorts": completed,
        **hac_mean_statistics(completed_values, lag=horizon - 1, family_size=family_size),
    }
    if not eligible:
        result.update(
            {
                "ci_lower": None,
                "ci_upper": None,
                "evidence": "pending",
            }
        )
    return result


def _signals_by_session(mapped: list[MappedSignal]) -> dict[int, list[MappedSignal]]:
    grouped: dict[int, list[MappedSignal]] = {}
    for item in mapped:
        grouped.setdefault(item.start_index, []).append(item)
    return grouped


def _target_weights(
    mapped_by_session: dict[int, list[MappedSignal]],
    day_index: int,
    horizon: int,
    exposure_pct: int,
) -> tuple[dict[str, float], list[MappedSignal]]:
    active_groups = [
        mapped_by_session[start_index]
        for start_index in range(max(0, day_index - horizon + 1), day_index + 1)
        if start_index in mapped_by_session
    ]
    active = [item for group in active_groups for item in group]
    session_sleeve = exposure_pct / 100.0 / horizon
    target: dict[str, float] = {}
    for group in active_groups:
        signal_sleeve = session_sleeve / len(group)
        for item in group:
            for position in item.signal.positions:
                if position.weight_pct > 0:
                    target[position.symbol] = (
                        target.get(position.symbol, 0.0) + signal_sleeve * position.weight_pct / 100.0
                    )
    equity_weight = sum(target.values())
    target["SPY"] = target.get("SPY", 0.0) + max(0.0, 1.0 - equity_weight)
    return target, active


def _daily_return(lookup: _Lookup, previous_day: str, day: str) -> float:
    previous = lookup.at(previous_day)
    current = lookup.at(day)
    if previous is None or current is None:
        raise RebuiltValuationError(f"No price at or before {previous_day} and {day}")
    return current / previous - 1.0


def construct_policy(
    signals: list[SignalInput],
    prices: dict[str, Series],
    calendar: list[str],
    horizon: int,
    exposure_pct: int,
    cost_bps: int,
    cost_basis: CostBasis,
) -> PolicyResult:
    """Construct one overlapping-cohort aggregate policy."""
    if horizon not in HORIZONS:
        raise ValueError("horizon must be between 1 and 20")
    if exposure_pct not in EXPOSURES:
        raise ValueError("exposure_pct must be 10, 20, …, 100")
    if cost_basis not in ("gross", "net"):
        raise ValueError("cost_basis must be gross or net")
    mapped = map_signals(signals, calendar)
    if not mapped:
        return PolicyResult(
            horizon=horizon,
            exposure_pct=exposure_pct,
            cost_basis=cost_basis,
            series=[],
            spy_series=[],
            daily_returns=[],
            holdings=[],
            active_cohorts=[],
            cumulative_cost=0.0,
            cumulative_turnover_pct=0.0,
        )

    lookups = {symbol: _Lookup(points) for symbol, points in prices.items()}
    if "SPY" not in lookups:
        raise RebuiltValuationError("SPY price series is required")
    first_index = min(item.start_index for item in mapped)
    mapped_by_session = _signals_by_session(mapped)
    nav = 100.0
    cumulative_cost = 0.0
    cumulative_turnover = 0.0
    drifted = {"SPY": 1.0}
    series: Series = []
    spy_series: Series = []
    daily: list[dict] = []
    previous_day: str | None = None
    target: dict[str, float] = {"SPY": 1.0}
    active: list[MappedSignal] = []
    deferred_cost = 0.0
    deferred_turnover_pct = 0.0

    for day_index in range(first_index, len(calendar)):
        day = calendar[day_index]
        before_return = 100.0 if previous_day is not None and not daily else nav
        if previous_day is not None:
            values: dict[str, float] = {}
            for symbol, weight in target.items():
                lookup = lookups.get(symbol)
                if lookup is None:
                    raise RebuiltValuationError(f"No price series for {symbol}")
                values[symbol] = weight * (1.0 + _daily_return(lookup, previous_day, day))
            gross_factor = sum(values.values())
            nav *= gross_factor
            drifted = (
                {symbol: value / gross_factor for symbol, value in values.items()} if gross_factor > 0 else {}
            )

        next_target, active = _target_weights(
            mapped_by_session,
            day_index,
            horizon,
            exposure_pct,
        )
        symbols = set(drifted) | set(next_target)
        turnover = 0.5 * sum(
            abs(next_target.get(symbol, 0.0) - drifted.get(symbol, 0.0)) for symbol in symbols
        )
        cost = nav * 2.0 * turnover * cost_bps / 10_000.0 if cost_basis == "net" else 0.0
        nav -= cost
        cumulative_cost += cost
        cumulative_turnover += turnover * 100.0

        if previous_day is not None:
            spy_return = _daily_return(lookups["SPY"], previous_day, day)
            strategy_return = nav / before_return - 1.0 if before_return > 0 else 0.0
            reported_cost = cost + deferred_cost
            reported_turnover_pct = turnover * 100.0 + deferred_turnover_pct
            daily.append(
                {
                    "date": day,
                    "return": strategy_return,
                    "spy_return": spy_return,
                    "alpha": strategy_return - spy_return,
                    "turnover_pct": reported_turnover_pct,
                    "cost": reported_cost,
                }
            )
            deferred_cost = 0.0
            deferred_turnover_pct = 0.0
        else:
            # There is no return observation at the entry close. Carry its
            # trade cost into the first actual close-to-close observation so
            # every net statistic and objective includes the entry trade.
            deferred_cost = cost
            deferred_turnover_pct = turnover * 100.0
        series.append({"date": day, "nav": nav})
        spy_close = lookups["SPY"].at(day)
        if spy_close is not None:
            if not spy_series:
                spy_base = spy_close
            spy_series.append({"date": day, "nav": spy_close / spy_base * 100.0})
        target = next_target
        drifted = next_target
        previous_day = day

    last_index = len(calendar) - 1
    active_out = [
        {
            "signal_id": item.signal.id,
            "start_date": calendar[item.start_index],
            "end_date": (
                calendar[item.start_index + horizon] if item.start_index + horizon < len(calendar) else None
            ),
            "age_sessions": last_index - item.start_index,
            "positions": [
                {"symbol": position.symbol, "weight_pct": position.weight_pct}
                for position in item.signal.positions
                if position.weight_pct > 0
            ],
        }
        for item in active
    ]
    return PolicyResult(
        horizon=horizon,
        exposure_pct=exposure_pct,
        cost_basis=cost_basis,
        series=series,
        spy_series=spy_series,
        daily_returns=daily,
        holdings=[
            {"symbol": symbol, "weight_pct": weight * 100.0}
            for symbol, weight in sorted(target.items())
            if weight > 0
        ],
        active_cohorts=active_out,
        cumulative_cost=cumulative_cost,
        cumulative_turnover_pct=cumulative_turnover,
    )


def policy_metrics(
    result: PolicyResult,
    completion: dict,
    family_size: int = 1,
) -> dict:
    if not result.series:
        return {
            "has_data": False,
            "complete_count": completion["complete_count"],
            "open_count": completion["open_count"],
            "completion_ratio": completion["completion_ratio"],
            "eligible": completion["eligible"],
            **hac_mean_statistics(
                [],
                lag=result.horizon - 1,
                family_size=family_size,
            ),
        }
    returns = [point["return"] for point in result.daily_returns]
    alphas = [point["alpha"] for point in result.daily_returns]
    alpha_stats = hac_mean_statistics(alphas, lag=result.horizon - 1, family_size=family_size)
    mean_return = sum(returns) / len(returns) if returns else None
    return_std = _sample_std(returns)
    alpha_std = _sample_std(alphas)
    sharpe = (
        mean_return / return_std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if mean_return is not None and return_std > 0
        else None
    )
    information_ratio = (
        alpha_stats["mean_daily_alpha"] / alpha_std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if alpha_stats["mean_daily_alpha"] is not None and alpha_std > 0
        else None
    )
    peak = 100.0
    max_drawdown = 0.0
    for point in result.series:
        peak = max(peak, point["nav"])
        if peak > 0:
            max_drawdown = min(max_drawdown, point["nav"] / peak - 1.0)
    strategy_return = result.series[-1]["nav"] / 100.0 - 1.0
    spy_return = result.spy_series[-1]["nav"] / 100.0 - 1.0 if len(result.spy_series) > 1 else 0.0
    metrics = {
        "has_data": True,
        "start_date": result.series[0]["date"],
        "end_date": result.series[-1]["date"],
        "itd_return": strategy_return,
        "spy_return": spy_return,
        "cumulative_excess": strategy_return - spy_return,
        "ann_volatility": return_std * math.sqrt(TRADING_DAYS_PER_YEAR) if returns else None,
        "max_drawdown": max_drawdown,
        "turnover_pct": result.cumulative_turnover_pct,
        "cost_drag_pct": result.cumulative_cost,
        "sharpe": sharpe,
        "information_ratio": information_ratio,
        "complete_count": completion["complete_count"],
        "open_count": completion["open_count"],
        "completion_ratio": completion["completion_ratio"],
        "eligible": completion["eligible"],
        **alpha_stats,
    }
    if not completion["eligible"]:
        metrics.update(
            {
                "ci_lower": None,
                "ci_upper": None,
                "evidence": "pending",
            }
        )
    return metrics


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def selected_objective_score(metrics: dict, objective: Objective) -> float | None:
    if objective == "canonical":
        return metrics.get("ci_lower")
    if objective == "max_alpha":
        return metrics.get("mean_daily_alpha")
    if objective == "max_information_ratio":
        return metrics.get("information_ratio")
    if objective == "max_sharpe":
        return metrics.get("sharpe")
    raise ValueError(f"Unknown objective: {objective}")


def select_policy(candidates: list[PolicyResult], objective: Objective) -> PolicyResult | None:
    """Select an eligible policy, resolving exact ties conservatively."""
    scored = [
        (selected_objective_score(candidate.metrics, objective), candidate)
        for candidate in candidates
        if candidate.metrics.get("eligible") and (objective != "canonical" or candidate.exposure_pct == 100)
    ]
    scored = [(score, candidate) for score, candidate in scored if score is not None]
    if not scored:
        return None
    return max(
        scored,
        key=lambda item: (
            item[0],
            -item[1].exposure_pct,
            -item[1].horizon,
        ),
    )[1]


def evaluate_policy_grid(
    signals: list[SignalInput],
    prices: dict[str, Series],
    calendar: list[str],
    cost_bps: int,
    cost_basis: CostBasis,
    objective: Objective,
) -> tuple[list[dict], list[PolicyResult], PolicyResult | None]:
    """Evaluate the direct H=1..20 matrix and all admissible policies."""
    horizon_stats = [
        signal_horizon_statistics(
            signals,
            prices,
            calendar,
            horizon,
            family_size=DIRECT_SEARCH_FAMILY_SIZE,
        )
        for horizon in HORIZONS
    ]

    family_size = DIRECT_SEARCH_FAMILY_SIZE if objective == "canonical" else OPTIMIZED_SEARCH_FAMILY_SIZE
    policies: list[PolicyResult] = []
    completion_by_horizon = {item["horizon"]: item for item in horizon_stats}
    for horizon in HORIZONS:
        for exposure in EXPOSURES:
            result = construct_policy(
                signals,
                prices,
                calendar,
                horizon,
                exposure,
                cost_bps,
                cost_basis,
            )
            result.metrics = policy_metrics(
                result,
                completion_by_horizon[horizon],
                family_size=family_size,
            )
            policies.append(result)
    return horizon_stats, policies, select_policy(policies, objective)
