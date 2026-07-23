"""Response shaping shared by public and admin routes."""

from datetime import UTC, datetime

from ..config import BENCHMARK_IDENTITY, BENCHMARK_STRATEGY
from ..models import Allocation, Portfolio
from .arena import ArenaValuations, PortfolioValuation, age_days, downsample, too_early
from .model_catalog import agent_out
from .prompt_policy import (
    allocation_policy_from_limits,
    allocation_policy_out,
    manual_execution_prompt,
)
from .trading_calendar import is_locked
from .valuation import AppliedAllocation, rebase_series


def agent_ref(portfolio: Portfolio) -> dict:
    if portfolio.is_benchmark:
        return {
            "id": None,
            "slug": BENCHMARK_IDENTITY["slug"],
            "name": BENCHMARK_IDENTITY["name"],
            "model": None,
            "harness": None,
            "execution_model_id": None,
            "reasoning_effort": None,
        }
    agent = portfolio.agent
    if agent is None:
        raise ValueError("Contestant portfolio is missing its agent")
    result = agent_out(agent)
    result.pop("notes", None)
    return result


def prompt_ref(portfolio: Portfolio) -> dict:
    if portfolio.is_benchmark:
        return {
            "id": None,
            "slug": BENCHMARK_STRATEGY["slug"],
            "name": BENCHMARK_STRATEGY["name"],
            "configurable": False,
            "allocation_policy": allocation_policy_from_limits(
                BENCHMARK_STRATEGY["min_position_weight_pct"],
                BENCHMARK_STRATEGY["max_position_weight_pct"],
            ),
        }
    prompt = portfolio.prompt
    if prompt is None:
        raise ValueError("Contestant portfolio is missing its prompt")
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "name": prompt.name,
        "configurable": True,
        "allocation_policy": allocation_policy_out(prompt),
    }


def allocation_positions(allocation: Allocation, admin: bool = False) -> list[dict]:
    return [
        {
            "symbol": position.symbol,
            "weight_pct": float(position.weight_pct),
            # Per-stock notes are admin-only — never exposed on public payloads.
            **({"note": position.note} if admin else {}),
        }
        for position in allocation.positions
    ]


def serialize_allocation(
    allocation: Allocation,
    applied: AppliedAllocation | None = None,
    now: datetime | None = None,
    admin: bool = False,
) -> dict:
    now = now or datetime.now(UTC)
    return {
        "id": allocation.id,
        "portfolio_id": allocation.portfolio_id,
        "entered_at": allocation.entered_at.isoformat(),
        "effective_date": allocation.effective_date.isoformat(),
        "applied_date": applied.applied_date if applied else None,
        "locked": is_locked(allocation.effective_date, now),
        "note": allocation.note,
        "turnover_pct": applied.turnover_pct if applied else None,
        "cost": applied.cost if applied else None,
        "positions": allocation_positions(allocation, admin),
    }


def _flags(valuation: PortfolioValuation) -> dict:
    result = valuation.result
    return {
        "stale_data": bool(result and result.stale_days),
        "frozen_symbols": result.frozen_symbols if result else [],
        "error": valuation.error,
    }


def serialize_summary(valuation: PortfolioValuation, valuations: ArenaValuations) -> dict:
    portfolio = valuation.portfolio
    allocations = portfolio.allocations
    age = age_days(valuation, valuations.as_of)
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "agent": agent_ref(portfolio),
        "prompt": prompt_ref(portfolio),
        "is_benchmark": portfolio.is_benchmark,
        "status": portfolio.status,
        "cost_bps": portfolio.cost_bps,
        "inception": valuation.result.series[0]["date"]
        if valuation.result and valuation.result.series
        else None,
        "age_days": age,
        "too_early": too_early(age),
        "allocation_count": len(allocations),
        "metrics": valuation.metrics,
        "sparkline": downsample(valuation.result.series) if valuation.result else [],
        **_flags(valuation),
    }


def serialize_detail(valuation: PortfolioValuation, valuations: ArenaValuations, admin: bool = False) -> dict:
    portfolio = valuation.portfolio
    result = valuation.result
    now = datetime.now(UTC)

    applied_by_date: dict[str, AppliedAllocation] = {}
    if result:
        for applied in result.allocations:
            applied_by_date[applied.effective_date] = applied

    series = result.series if result else []
    spy_overlay = []
    if series:
        spy_overlay = rebase_series(valuations.spy_series, series[0]["date"], series[-1]["date"])

    return {
        **serialize_summary(valuation, valuations),
        "execution_prompt": None if portfolio.is_benchmark else manual_execution_prompt(portfolio),
        "series": series,
        "spy_series": spy_overlay,
        "holdings": [
            {
                "symbol": holding.symbol,
                "weight_pct": holding.weight_pct,
                "target_weight_pct": holding.target_weight_pct,
                # Buy/current price and per-stock note are admin-only handoff fields.
                **(
                    {
                        "entry_price": holding.entry_price,
                        "current_price": holding.current_price,
                        "note": holding.note,
                    }
                    if admin
                    else {}
                ),
            }
            for holding in (result.holdings if result else [])
        ],
        "stale_days": result.stale_days if result else {},
        "allocations": [
            serialize_allocation(
                allocation, applied_by_date.get(allocation.effective_date.isoformat()), now, admin
            )
            for allocation in reversed(portfolio.allocations)
        ],
    }
