"""Response shaping shared by public and admin routes."""
from datetime import UTC, datetime

from ..models import Allocation, Portfolio
from ..services.arena import ArenaValuations, PortfolioValuation, age_days, downsample, too_early
from ..services.trading_calendar import is_locked
from ..services.valuation import AppliedAllocation, rebase_series


def agent_ref(portfolio: Portfolio) -> dict:
    return {"id": portfolio.agent.id, "slug": portfolio.agent.slug, "name": portfolio.agent.name}


def prompt_ref(allocation: Allocation | None) -> dict | None:
    if allocation is None:
        return None
    prompt = allocation.prompt
    return {"id": prompt.id, "slug": prompt.slug, "name": prompt.name}


def allocation_positions(allocation: Allocation) -> list[dict]:
    return [
        {
            "symbol": position.symbol,
            "instrument": position.instrument,
            "weight_pct": float(position.weight_pct),
        }
        for position in allocation.positions
    ]


def serialize_allocation(
    allocation: Allocation, applied: AppliedAllocation | None = None, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    return {
        "id": allocation.id,
        "portfolio_id": allocation.portfolio_id,
        "prompt": prompt_ref(allocation),
        "entered_at": allocation.entered_at.isoformat(),
        "effective_date": allocation.effective_date.isoformat(),
        "applied_date": applied.applied_date if applied else None,
        "locked": is_locked(allocation.effective_date, now),
        "note": allocation.note,
        "raw_response": allocation.raw_response,
        "turnover_pct": applied.turnover_pct if applied else None,
        "cost": applied.cost if applied else None,
        "positions": allocation_positions(allocation),
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
    latest = allocations[-1] if allocations else None
    age = age_days(valuation, valuations.as_of)
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "agent": agent_ref(portfolio),
        "prompt": prompt_ref(latest),
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


def serialize_detail(valuation: PortfolioValuation, valuations: ArenaValuations) -> dict:
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
        "series": series,
        "spy_series": spy_overlay,
        "holdings": [
            {
                "symbol": holding.symbol,
                "instrument": holding.instrument,
                "weight_pct": holding.weight_pct,
                "target_weight_pct": holding.target_weight_pct,
            }
            for holding in (result.holdings if result else [])
        ],
        "stale_days": result.stale_days if result else {},
        "allocations": [
            serialize_allocation(
                allocation, applied_by_date.get(allocation.effective_date.isoformat()), now
            )
            for allocation in reversed(portfolio.allocations)
        ],
    }
