"""Response shaping shared by public, admin, and MCP routes."""

from __future__ import annotations

from datetime import UTC, datetime

from ..models import Allocation, Portfolio, Signal
from .arena import (
    ArenaValuations,
    PortfolioValuation,
    RebuiltArena,
    RebuiltPortfolioAnalysis,
    age_days,
    downsample,
)
from .model_catalog import agent_out
from .prompt_policy import manual_execution_prompt
from .trading_calendar import boundary_value, is_locked
from .valuation import (
    AppliedAllocation,
    Boundary,
    Direction,
    ValuationError,
    build_calendar,
    point_boundary,
    rebase_series,
    series_metrics,
)


def agent_ref(portfolio: Portfolio) -> dict:
    result = agent_out(portfolio.agent)
    result.pop("notes", None)
    return result


def prompt_ref(portfolio: Portfolio, allocation_policy: dict) -> dict:
    return {
        "id": portfolio.prompt.id,
        "slug": portfolio.prompt.slug,
        "name": portfolio.prompt.name,
        "mode": portfolio.prompt.mode,
        "direction": portfolio.prompt.direction,
        "configurable": True,
        "allocation_policy": allocation_policy,
    }


def version_ref(portfolio: Portfolio) -> dict:
    version = portfolio.version
    return {
        "id": version.id,
        "name": version.name,
        "evaluation_enabled": version.evaluation_enabled,
        "created_at": version.created_at.isoformat(),
    }


def _identity(portfolio: Portfolio, allocation_policy: dict) -> dict:
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "version_id": portfolio.version_id,
        "version": version_ref(portfolio),
        "execution_boundary": portfolio.execution_boundary,
        "agent": agent_ref(portfolio),
        "prompt": prompt_ref(portfolio, allocation_policy),
        "prompt_mode": portfolio.prompt_mode,
        "direction": portfolio.direction,
    }


def allocation_positions(allocation: Allocation, admin: bool = False) -> list[dict]:
    return [
        {
            "symbol": position.symbol,
            "weight_pct": float(position.weight_pct),
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
    phase = allocation.portfolio.execution_boundary
    return {
        "id": allocation.id,
        "portfolio_id": allocation.portfolio_id,
        "entered_at": allocation.entered_at.isoformat(),
        "effective_at": boundary_value(allocation.effective_date, phase),
        "applied_at": applied.applied_at if applied else None,
        "locked": is_locked(allocation.effective_date, now or datetime.now(UTC), phase),
        "note": allocation.note,
        "turnover_pct": applied.turnover_pct if applied else None,
        "positions": allocation_positions(allocation, admin),
    }


def serialize_signal(signal: Signal, *, admin: bool = False, now: datetime | None = None) -> dict:
    phase = signal.portfolio.execution_boundary
    return {
        "id": signal.id,
        "portfolio_id": signal.portfolio_id,
        "entered_at": signal.entered_at.isoformat(),
        "effective_at": boundary_value(signal.effective_date, phase),
        "locked": is_locked(signal.effective_date, now or datetime.now(UTC), phase),
        "note": signal.note,
        **({"provenance": signal.provenance} if admin else {}),
        "positions": [
            {
                "symbol": position.symbol,
                "weight_pct": float(position.weight_pct),
                **({"note": position.note} if admin else {}),
            }
            for position in signal.positions
        ],
    }


def serialize_summary(
    valuation: PortfolioValuation, valuations: ArenaValuations, allocation_policy: dict
) -> dict:
    portfolio = valuation.portfolio
    result = valuation.result
    return {
        **_identity(portfolio, allocation_policy),
        "kind": "managed",
        "rank": None,
        "inception": point_boundary(result.series[0]) if result and result.series else None,
        "age_days": age_days(valuation, valuations.current_date),
        "allocation_count": len(portfolio.allocations),
        "evidence": valuation.metrics.get("evidence", "pending"),
        "rank_score": valuation.metrics.get("ci_lower"),
        "metrics": valuation.metrics,
        "sparkline": downsample(result.series) if result else [],
        "stale_data": bool(result and result.stale_days),
        "frozen_symbols": result.frozen_symbols if result else [],
        "is_liquidated": bool(result and result.liquidated_at),
        "liquidated_at": result.liquidated_at if result else None,
        "error": valuation.error,
    }


def serialize_detail(
    valuation: PortfolioValuation,
    valuations: ArenaValuations,
    allocation_policy: dict,
    direction_instructions: str,
    admin: bool = False,
    wrapper_prompt: str | None = None,
) -> dict:
    portfolio = valuation.portfolio
    result = valuation.result
    series = result.series if result else []
    applied = {item.effective_date: item for item in result.allocations} if result else {}
    return {
        **serialize_summary(valuation, valuations, allocation_policy),
        "execution_prompt": manual_execution_prompt(
            portfolio, wrapper_prompt or "", direction_instructions, allocation_policy
        ),
        "series": series,
        "spy_series": rebase_series(
            valuations.spy_series, point_boundary(series[0]), point_boundary(series[-1]), portfolio.direction
        )
        if series
        else [],
        "holdings": [
            {
                "symbol": holding.symbol,
                "weight_pct": holding.weight_pct,
                "target_weight_pct": holding.target_weight_pct,
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
            serialize_allocation(item, applied.get(item.effective_date.isoformat()), admin=admin)
            for item in reversed(portfolio.allocations)
        ],
    }


def synthetic_spy_row(
    spy_series: list[dict],
    start: Boundary | None = None,
    *,
    direction: Direction = "long",
    as_of: Boundary | None = None,
) -> dict:
    series = []
    if spy_series and start is not None:
        if as_of is None:
            from datetime import date

            last = max(spy_series, key=lambda point: point["date"])
            as_of = boundary_value(
                date.fromisoformat(last["date"]), "close" if last.get("close") is not None else "open"
            )
        calendar = build_calendar(spy_series, as_of)
        if calendar:
            try:
                series = rebase_series(spy_series, start or calendar[0], as_of, direction)
            except ValuationError:
                series = []
    liquidated_at = next((point_boundary(point) for point in series if point["nav"] <= 0), None)
    metrics = series_metrics(series, series, 0, liquidated_at)
    metrics.update(
        {
            "mean_daily_alpha": 0.0 if len(series) > 1 else None,
            "ci_lower": 0.0 if len(series) > 1 else None,
            "ci_upper": 0.0 if len(series) > 1 else None,
            "evidence": "inconclusive" if len(series) > 1 else "pending",
        }
    )
    return {
        "kind": "benchmark",
        "id": None,
        "slug": "spy",
        "name": "Short SPY" if direction == "short" else "SPY",
        "direction": direction,
        "rank": None,
        "evidence": metrics["evidence"],
        "rank_score": None,
        "metrics": metrics,
        "sparkline": downsample(series),
        "is_liquidated": bool(liquidated_at),
        "liquidated_at": liquidated_at,
    }


def rank_rows(rows: list[dict]) -> list[dict]:
    eligible = sorted(
        (row for row in rows if row.get("kind") != "benchmark" and row.get("rank_score") is not None),
        key=lambda row: (-row["rank_score"], row["name"].casefold(), row["id"]),
    )
    for rank, row in enumerate(eligible, 1):
        row["rank"] = rank
    return rows


def _public_horizon(item: dict) -> dict:
    return {
        **{key: value for key, value in item.items() if key != "completed_cohorts"},
        "has_data": bool(item.get("complete_count") or item.get("open_count")),
    }


def serialize_rebuilt_summary(
    analysis: RebuiltPortfolioAnalysis, arena: RebuiltArena, allocation_policy: dict
) -> dict:
    policy = analysis.selected
    metrics = policy.metrics if policy else {"has_data": False, "evidence": "pending"}
    completion = next(
        (item for item in analysis.signal_horizons if policy and item["horizon"] == policy.horizon), {}
    )
    return {
        **_identity(analysis.portfolio, allocation_policy),
        "kind": "rebuilt",
        "optimization_objective": arena.objective,
        "rank": None,
        "evidence": metrics.get("evidence", "pending"),
        "rank_score": metrics.get("ci_lower") if metrics.get("eligible") else None,
        "metrics": metrics,
        "inception": point_boundary(policy.series[0]) if policy and policy.series else None,
        "selected_policy": {"horizon": policy.horizon} if policy else None,
        "is_liquidated": bool(policy and policy.liquidated_at),
        "liquidated_at": policy.liquidated_at if policy else None,
        "completion": {
            key: completion.get(key, default)
            for key, default in (
                ("complete_count", 0),
                ("open_count", 0),
                ("completion_ratio", 0.0),
                ("eligible", False),
            )
        },
        "signal_horizons": [_public_horizon(item) for item in analysis.signal_horizons],
        "sparkline": downsample(policy.series) if policy else [],
        "error": analysis.error,
        "stale_data": analysis.stale_data,
        "frozen_symbols": analysis.frozen_symbols,
    }


def serialize_rebuilt_detail(
    analysis: RebuiltPortfolioAnalysis,
    arena: RebuiltArena,
    allocation_policy: dict,
    direction_instructions: str,
    *,
    admin: bool = False,
    wrapper_prompt: str = "",
) -> dict:
    policy = analysis.selected
    signals = sorted(analysis.portfolio.signals, key=lambda item: item.id, reverse=True)[:20]
    return {
        **serialize_rebuilt_summary(analysis, arena, allocation_policy),
        "execution_prompt": manual_execution_prompt(
            analysis.portfolio, wrapper_prompt, direction_instructions, allocation_policy
        ),
        "series": policy.series if policy else [],
        "spy_series": policy.spy_series if policy else [],
        "holdings": policy.holdings if policy else [],
        "active_cohorts": policy.active_cohorts if policy else [],
        "signals": [serialize_signal(signal, admin=admin) for signal in signals],
        "signals_next_cursor": signals[-1].id if len(analysis.portfolio.signals) > len(signals) else None,
    }
