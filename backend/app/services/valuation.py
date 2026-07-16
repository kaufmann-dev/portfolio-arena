"""Deterministic paper-portfolio valuation. The correctness core of the app.

Pure functions only: same allocations + same price series => identical output.
No wall-clock reads — callers pass every date in. All NAV series are base-100
at the portfolio's first effective close.

Conventions:
- USD-denominated equities and ETFs hold fractional shares.
- Entry cost at inception applies to the fully invested NAV.
- Rebalance: turnover = 0.5 * sum(|w_new - w_drift|) over all symbols,
  cost = NAV * 2 * (turnover/100) * bps/1e4 (both sides pay), then all state
  resets from the new weights on the post-cost NAV.
- Calendar = days SPY has a close. Missing prices carry forward and are
  flagged; nothing is guessed silently.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import timedelta

TRADING_DAYS_PER_YEAR = 252
# A held symbol with no fresh print for this many trading days is presumed
# delisted/halted and surfaced as frozen.
FROZEN_AFTER_TRADING_DAYS = 5

Series = list[dict]  # [{"date": "YYYY-MM-DD", "close": float}, ...]


@dataclass(frozen=True)
class PositionInput:
    symbol: str
    weight_pct: float
    note: str = ""  # admin-only per-stock message, carried between cycles


@dataclass(frozen=True)
class AllocationInput:
    effective_date: str  # ISO date
    positions: tuple[PositionInput, ...]


@dataclass
class AppliedAllocation:
    effective_date: str
    applied_date: str | None  # calendar date the state reset happened; None = pending
    turnover_pct: float | None  # one-sided, % of NAV; None for the initial allocation
    cost: float  # NAV points (base-100 units)
    nav_before: float | None
    nav_after: float | None


@dataclass
class Holding:
    symbol: str
    weight_pct: float  # drifted, as of the last valued day
    target_weight_pct: float  # from the latest applied allocation
    value: float  # NAV points
    entry_price: float | None = None
    current_price: float | None = None
    note: str = ""  # per-stock note from the latest applied allocation


@dataclass
class ValuationResult:
    series: Series  # [{"date", "nav"}]
    allocations: list[AppliedAllocation]
    holdings: list[Holding]
    stale_days: dict[str, list[str]] = field(default_factory=dict)  # symbol -> dates carried
    frozen_symbols: list[str] = field(default_factory=list)
    cumulative_cost: float = 0.0  # NAV points spent on costs
    cumulative_turnover_pct: float = 0.0  # sum of one-sided rebalance turnover


class ValuationError(Exception):
    """A portfolio cannot be valued from the given inputs (e.g. a symbol has
    no price at or before its first effective close)."""


class _PriceLookup:
    """Last-known-close lookup over a daily series."""

    def __init__(self, points: Series):
        cleaned = sorted(
            (p["date"], float(p["close"]))
            for p in points or []
            if p.get("close") is not None and math.isfinite(float(p["close"]))
        )
        self.dates = [d for d, _ in cleaned]
        self.closes = [c for _, c in cleaned]

    def at(self, day: str) -> tuple[float, bool] | None:
        """(close, exact) for the last print on or before `day`, or None."""
        index = bisect_right(self.dates, day)
        if index == 0:
            return None
        return self.closes[index - 1], self.dates[index - 1] == day

    def last_date_at_or_before(self, day: str) -> str | None:
        index = bisect_right(self.dates, day)
        return self.dates[index - 1] if index else None


def build_calendar(spy_series: Series, as_of: str) -> list[str]:
    """Trading calendar = days SPY has a close, capped at as_of."""
    return sorted({p["date"] for p in spy_series if p["date"] <= as_of})


def value_portfolio(
    allocations: list[AllocationInput],
    cost_bps: int,
    prices: dict[str, Series],
    calendar: list[str],
    as_of: str,
) -> ValuationResult:
    """Value one portfolio over `calendar` up to `as_of` (inclusive)."""
    calendar = [d for d in calendar if d <= as_of]
    allocations = sorted(allocations, key=lambda a: a.effective_date)
    lookups = {symbol: _PriceLookup(points) for symbol, points in prices.items()}

    applied: list[AppliedAllocation] = []
    # Map each allocation to the first calendar date >= its effective date
    # (an unscheduled closure shifts the effective close to the next actual one).
    schedule: dict[str, list[AllocationInput]] = {}
    for allocation in allocations:
        index = bisect_right(calendar, allocation.effective_date)
        if index and calendar[index - 1] == allocation.effective_date:
            index -= 1
        if index >= len(calendar):
            applied.append(
                AppliedAllocation(
                    effective_date=allocation.effective_date,
                    applied_date=None,
                    turnover_pct=None,
                    cost=0.0,
                    nav_before=None,
                    nav_after=None,
                )
            )
            continue
        schedule.setdefault(calendar[index], []).append(allocation)
    pending = list(applied)  # keep pending entries; applied ones are appended in date order
    applied = []

    if not schedule:
        return ValuationResult(series=[], allocations=pending, holdings=[])

    first_day = min(schedule)
    day_index = calendar.index(first_day)

    # Portfolio state
    shares: dict[str, float] = {}
    targets: dict[str, PositionInput] = {}
    entry_prices: dict[str, float] = {}
    nav = 100.0
    cumulative_cost = 0.0
    cumulative_turnover = 0.0
    stale_days: dict[str, list[str]] = {}
    series: Series = []

    def lookup(symbol: str, day: str, holder: str) -> float:
        """Price lookup with carry-forward; flags stale days."""
        entry = lookups.get(symbol)
        result = entry.at(day) if entry else None
        if result is None:
            raise ValuationError(f"No price for {holder} at or before {day}.")
        close, exact = result
        if not exact:
            stale_days.setdefault(holder, []).append(day)
        return close

    def position_value(day: str) -> float:
        return sum(quantity * lookup(symbol, day, symbol) for symbol, quantity in shares.items())

    def apply_allocation(allocation: AllocationInput, day: str, first: bool) -> None:
        nonlocal nav, cumulative_cost, cumulative_turnover, shares, targets, entry_prices
        positions = [p for p in allocation.positions if p.weight_pct > 0]

        if first:
            nav_before = 100.0
            cost = nav_before * cost_bps / 10_000.0
            turnover_pct = None
        else:
            nav_before = position_value(day)
            drifted = _drifted_weights(day)
            new_weights = {p.symbol: p.weight_pct for p in positions}
            symbols = set(drifted) | set(new_weights)
            turnover_pct = 0.5 * sum(abs(new_weights.get(s, 0.0) - drifted.get(s, 0.0)) for s in symbols)
            cost = nav_before * 2 * (turnover_pct / 100.0) * cost_bps / 10_000.0
            cumulative_turnover += turnover_pct

        nav_after = nav_before - cost
        cumulative_cost += cost

        shares = {}
        entry_prices = {}
        for position in positions:
            value = nav_after * position.weight_pct / 100.0
            price = lookup(position.symbol, day, position.symbol)
            shares[position.symbol] = value / price
            entry_prices[position.symbol] = price

        targets = {p.symbol: p for p in positions}
        nav = nav_after
        applied.append(
            AppliedAllocation(
                effective_date=allocation.effective_date,
                applied_date=day,
                turnover_pct=turnover_pct,
                cost=cost,
                nav_before=nav_before,
                nav_after=nav_after,
            )
        )

    def _drifted_weights(day: str) -> dict[str, float]:
        """Drifted position weights as a percentage of NAV at `day`."""
        total = position_value(day)
        if total <= 0:
            return {}
        return {
            symbol: quantity * lookup(symbol, day, symbol) / total * 100.0
            for symbol, quantity in shares.items()
        }

    first_seen = False
    for day in calendar[day_index:]:
        for allocation in schedule.get(day, ()):
            apply_allocation(allocation, day, first=not first_seen)
            first_seen = True
        nav = position_value(day)
        series.append({"date": day, "nav": nav})

    # As-of holdings (drifted weights on the last valued day)
    holdings: list[Holding] = []
    last_day = series[-1]["date"]
    last_nav = series[-1]["nav"]
    if last_nav > 0:
        for symbol, quantity in sorted(shares.items()):
            current_price = lookup(symbol, last_day, symbol)
            value = quantity * current_price
            holdings.append(
                Holding(
                    symbol=symbol,
                    weight_pct=value / last_nav * 100.0,
                    target_weight_pct=targets[symbol].weight_pct if symbol in targets else 0.0,
                    value=value,
                    entry_price=entry_prices.get(symbol),
                    current_price=current_price,
                    note=targets[symbol].note if symbol in targets else "",
                )
            )

    # Frozen symbols: still held, but no fresh print for a while.
    frozen: list[str] = []
    recent = calendar[-FROZEN_AFTER_TRADING_DAYS:]
    if recent:
        threshold = recent[0]
        for symbol in shares:
            entry = lookups.get(symbol)
            last_print = entry.last_date_at_or_before(last_day) if entry else None
            if last_print is None or last_print < threshold:
                frozen.append(symbol)

    # De-duplicate stale day lists (a day can be hit by several valuations).
    deduped_stale = {symbol: sorted(set(days)) for symbol, days in stale_days.items()}

    return ValuationResult(
        series=series,
        allocations=applied + pending,
        holdings=holdings,
        stale_days=deduped_stale,
        frozen_symbols=sorted(frozen),
        cumulative_cost=cumulative_cost,
        cumulative_turnover_pct=cumulative_turnover,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

TRAILING_WINDOWS = {"r1m": 30, "r3m": 91, "r6m": 182, "r1y": 365}


def _daily_returns(series: Series) -> list[float]:
    returns = []
    for previous, current in zip(series, series[1:], strict=False):
        if previous["nav"] > 0:
            returns.append(current["nav"] / previous["nav"] - 1.0)
    return returns


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _date_add_days(day: str, days: int) -> str:
    return (date_type.fromisoformat(day) + timedelta(days=days)).isoformat()


def _nav_at_or_before(series: Series, day: str) -> float | None:
    best = None
    for point in series:
        if point["date"] <= day:
            best = point["nav"]
        else:
            break
    return best


def compute_metrics(result: ValuationResult, spy_series: Series) -> dict:
    """Portfolio metrics from the base-100 series; SPY compared over the
    identical window using its adjusted closes."""
    series = result.series
    if not series:
        return {"has_data": False}

    first_day, last_day = series[0]["date"], series[-1]["date"]
    last_nav = series[-1]["nav"]

    itd_return = last_nav / 100.0 - 1.0

    spy_lookup = _PriceLookup(spy_series)
    spy_return = None
    spy_start = spy_lookup.at(first_day)
    spy_end = spy_lookup.at(last_day)
    if spy_start and spy_end and spy_start[0] > 0:
        spy_return = spy_end[0] / spy_start[0] - 1.0

    returns = _daily_returns(series)
    volatility = _std(returns) * math.sqrt(TRADING_DAYS_PER_YEAR) if returns else None
    sharpe = None
    if returns:
        std = _std(returns)
        if std > 0:
            mean = sum(returns) / len(returns)
            sharpe = mean / std * math.sqrt(TRADING_DAYS_PER_YEAR)

    peak = -math.inf
    max_drawdown = 0.0
    for point in series:
        peak = max(peak, point["nav"])
        if peak > 0:
            max_drawdown = min(max_drawdown, point["nav"] / peak - 1.0)

    trailing: dict[str, float | None] = {}
    for key, days in TRAILING_WINDOWS.items():
        anchor = _date_add_days(last_day, -days)
        if first_day <= anchor:
            base = _nav_at_or_before(series, anchor)
            trailing[key] = last_nav / base - 1.0 if base else None
        else:
            trailing[key] = None

    return {
        "has_data": True,
        "start_date": first_day,
        "end_date": last_day,
        "itd_return": itd_return,
        "spy_return": spy_return,
        "vs_spy": itd_return - spy_return if spy_return is not None else None,
        "ann_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "cost_drag_pct": result.cumulative_cost,
        "turnover_pct": result.cumulative_turnover_pct,
        **trailing,
    }


def rebase_series(points: Series, start: str, end: str) -> Series:
    """Base-100 a raw close series over [start, end] (for SPY overlays)."""
    window = [p for p in points if start <= p["date"] <= end]
    if not window:
        return []
    base = window[0]["close"]
    if not base:
        return []
    return [{"date": p["date"], "nav": p["close"] / base * 100.0} for p in window]
