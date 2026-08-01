"""Response shaping shared by public, admin, and MCP routes."""

from __future__ import annotations

from datetime import UTC, datetime

from ..models import Allocation, Portfolio, Signal
from .arena import (
    COMMON_INCUBATION_POLICY,
    ArenaValuations,
    PortfolioValuation,
    RebuiltArena,
    RebuiltPortfolioAnalysis,
    age_days,
    downsample,
)
from .model_catalog import agent_out
from .prompt_policy import manual_execution_prompt
from .rebuilt import PolicyResult, selected_objective_score
from .trading_calendar import is_locked
from .valuation import AppliedAllocation, Direction, rebase_series


def agent_ref(portfolio: Portfolio) -> dict:
    result = agent_out(portfolio.agent)
    result.pop("notes", None)
    return result


def prompt_ref(portfolio: Portfolio, allocation_policy: dict) -> dict:
    return {
        "id": portfolio.prompt.id,
        "slug": portfolio.prompt.slug,
        "name": portfolio.prompt.name,
        "context_scope": portfolio.prompt.context_scope,
        "mode": portfolio.prompt.mode,
        "direction": portfolio.prompt.direction,
        "configurable": True,
        "allocation_policy": allocation_policy,
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


def serialize_signal(
    signal: Signal,
    *,
    admin: bool = False,
    now: datetime | None = None,
) -> dict:
    """Serialize immutable signal inputs; provenance and position notes are private."""
    now = now or datetime.now(UTC)
    return {
        "id": signal.id,
        "portfolio_id": signal.portfolio_id,
        "entered_at": signal.entered_at.isoformat(),
        "effective_date": signal.effective_date.isoformat(),
        "locked": is_locked(signal.effective_date, now),
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


def _flags(valuation: PortfolioValuation) -> dict:
    result = valuation.result
    return {
        "stale_data": bool(result and result.stale_days),
        "frozen_symbols": result.frozen_symbols if result else [],
        "is_liquidated": bool(result and result.liquidated_at),
        "liquidated_at": result.liquidated_at if result else None,
        "error": valuation.error,
    }


def serialize_summary(
    valuation: PortfolioValuation,
    valuations: ArenaValuations,
    allocation_policy: dict,
) -> dict:
    """Managed-track summary retained for admin callers and public ranking."""
    portfolio = valuation.portfolio
    age = age_days(valuation, valuations.current_date)
    return {
        "kind": "managed",
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "agent": agent_ref(portfolio),
        "prompt": prompt_ref(portfolio, allocation_policy),
        "prompt_mode": portfolio.prompt_mode,
        "direction": portfolio.direction,
        "status": portfolio.status,
        "rank": None,
        "cost_bps": portfolio.cost_bps,
        "inception": (
            valuation.result.series[0]["date"] if valuation.result and valuation.result.series else None
        ),
        "age_days": age,
        "allocation_count": len(portfolio.allocations),
        "evidence": valuation.metrics.get("evidence", "pending"),
        "rank_score": valuation.metrics.get("ci_lower"),
        "metrics": valuation.metrics,
        "sparkline": downsample(valuation.result.series) if valuation.result else [],
        **_flags(valuation),
    }


def serialize_detail(
    valuation: PortfolioValuation,
    valuations: ArenaValuations,
    allocation_policy: dict,
    direction_instructions: str,
    admin: bool = False,
    wrapper_prompt: str | None = None,
) -> dict:
    """Managed detail, including preserved allocations and current holdings."""
    portfolio = valuation.portfolio
    result = valuation.result
    now = datetime.now(UTC)
    applied_by_date = {applied.effective_date: applied for applied in (result.allocations if result else [])}
    series = result.series if result else []
    spy_overlay = (
        rebase_series(
            valuations.spy_series,
            series[0]["date"],
            series[-1]["date"],
            direction=portfolio.direction,
        )
        if series
        else []
    )
    return {
        **serialize_summary(valuation, valuations, allocation_policy),
        "execution_prompt": manual_execution_prompt(
            portfolio,
            wrapper_prompt or "",
            direction_instructions,
            allocation_policy,
        ),
        "series": series,
        "spy_series": spy_overlay,
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
            serialize_allocation(
                allocation,
                applied_by_date.get(allocation.effective_date.isoformat()),
                now,
                admin,
            )
            for allocation in reversed(portfolio.allocations)
        ],
    }


def synthetic_spy_row(
    spy_series: list[dict],
    start: str | None = None,
    *,
    precomputed_nav: bool = False,
    direction: Direction = "long",
) -> dict:
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    points = [point for point in spy_series if start is None or point["date"] >= start]
    sparkline = []
    metrics = {
        "has_data": bool(points),
        "itd_return": None,
        "spy_return": None,
        "cumulative_excess": 0.0 if points else None,
        "mean_daily_alpha": 0.0 if len(points) > 1 else None,
        "ci_lower": 0.0 if len(points) > 1 else None,
        "ci_upper": 0.0 if len(points) > 1 else None,
        "evidence": "inconclusive" if len(points) > 1 else "pending",
        "liquidated_at": None,
    }
    if points:
        if precomputed_nav:
            base = float(points[0]["nav"])
            nav_points = (
                [{"date": point["date"], "nav": float(point["nav"]) / base * 100.0} for point in points]
                if base > 0
                else [{"date": point["date"], "nav": 0.0} for point in points]
            )
        else:
            nav_points = rebase_series(
                points,
                points[0]["date"],
                points[-1]["date"],
                direction=direction,
            )
        navs = [float(point["nav"]) for point in nav_points]
        sparkline = downsample(nav_points)
        liquidated_at = next(
            (point["date"] for point in nav_points if point["nav"] <= 0),
            None,
        )
        metrics.update(
            {
                "start_date": nav_points[0]["date"],
                "end_date": nav_points[-1]["date"],
                "itd_return": navs[-1] / 100.0 - 1.0,
                "spy_return": navs[-1] / 100.0 - 1.0,
                "liquidated_at": liquidated_at,
            }
        )
    return {
        "kind": "benchmark",
        "id": None,
        "slug": "spy",
        "name": "Short SPY" if direction == "short" else "SPY",
        "direction": direction,
        "status": "reference",
        "rank": None,
        "evidence": metrics["evidence"],
        "rank_score": None,
        "metrics": metrics,
        "sparkline": sparkline,
        "is_liquidated": bool(metrics["liquidated_at"]),
        "liquidated_at": metrics["liquidated_at"],
    }


def rank_rows(rows: list[dict]) -> list[dict]:
    """Rank active, evidenced contestants by adjusted lower confidence bound."""
    eligible = sorted(
        (
            row
            for row in rows
            if row.get("kind") != "benchmark"
            and row.get("status") == "active"
            and row.get("rank_score") is not None
        ),
        key=lambda row: (-row["rank_score"], row["name"].casefold(), row["id"]),
    )
    for rank, row in enumerate(eligible, start=1):
        row["rank"] = rank
    return rows


def _public_horizon(item: dict) -> dict:
    payload = {key: value for key, value in item.items() if key != "completed_cohorts"}
    payload["has_data"] = bool(item.get("complete_count") or item.get("open_count"))
    return payload


def _selected_policy_payload(
    policy: PolicyResult | None,
    objective: str,
    displayed_metrics: dict,
) -> dict | None:
    if policy is None:
        return None
    return {
        "horizon": policy.horizon,
        "exposure_pct": policy.exposure_pct,
        "objective_score": selected_objective_score(displayed_metrics, objective),
    }


def _aggregate_policy_payload(policy: PolicyResult | None, *, provisional: bool) -> dict | None:
    if policy is None:
        return None
    return {
        "horizon": policy.horizon,
        "exposure_pct": policy.exposure_pct,
        "provisional": provisional,
    }


def serialize_rebuilt_summary(
    analysis: RebuiltPortfolioAnalysis,
    arena: RebuiltArena,
    allocation_policy: dict,
    *,
    view: str,
    horizon: int | None = None,
) -> dict:
    portfolio = analysis.portfolio
    policy: PolicyResult | None
    direct: dict | None = None
    common = arena.common_for(portfolio.direction)
    common_admitted = portfolio.id in common.member_ids
    displayed_series: list[dict] = []
    common_pair = common.policy if view == "common" else None
    if view == "common":
        policy = (
            analysis.policies.get((common_pair["horizon"], common_pair["exposure_pct"]))
            if common_pair and common_admitted
            else None
        )
        if policy is not None:
            displayed_series = common.member_series.get(portfolio.id, [])
    elif view == "tuned":
        policy = analysis.selected
        displayed_series = policy.series if policy else []
    elif view == "signal":
        direct = next(
            (item for item in analysis.signal_horizons if item["horizon"] == horizon),
            None,
        )
        policy = analysis.policies.get((horizon, 100)) if horizon is not None else None
        displayed_series = policy.series if policy else []
    else:
        raise ValueError(f"Unknown rebuilt view: {view}")

    if view == "common":
        metrics = (common.member_metrics.get(portfolio.id) if policy is not None else None) or {
            "has_data": False,
            "eligible": False,
            "evidence": "pending",
            "ci_lower": None,
            "ci_upper": None,
        }
    else:
        metrics = (_public_horizon(direct) if direct is not None else None) or (
            policy.metrics if policy else {"has_data": False, "evidence": "pending"}
        )
    completion_horizon = policy.horizon if policy else None
    if view == "common" and completion_horizon is None and portfolio.status == "active":
        completion_horizon = COMMON_INCUBATION_POLICY[0]
    selected_horizon = direct or next(
        (item for item in analysis.signal_horizons if item["horizon"] == completion_horizon),
        None,
    )
    rank_score = (
        metrics.get("ci_lower")
        if metrics.get("eligible") is True and metrics.get("evidence") != "pending"
        else None
    )
    completion = {
        key: selected_horizon.get(key) if selected_horizon else default
        for key, default in (
            ("complete_count", 0),
            ("open_count", 0),
            ("completion_ratio", 0.0),
            ("eligible", False),
        )
    }
    return {
        "kind": "rebuilt",
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "agent": agent_ref(portfolio),
        "prompt": prompt_ref(portfolio, allocation_policy),
        "prompt_mode": "rebuilt",
        "direction": portfolio.direction,
        "status": portfolio.status,
        "cost_bps": portfolio.cost_bps,
        "founding_v2": portfolio.founding_v2,
        "common_admitted": common_admitted,
        "rank": None,
        "evidence": metrics.get("evidence", "pending"),
        "rank_score": rank_score,
        "metrics": metrics,
        "selected_policy": _selected_policy_payload(policy, arena.objective, metrics),
        "is_liquidated": bool(policy and policy.liquidated_at),
        "liquidated_at": policy.liquidated_at if policy else None,
        "completion": completion,
        "signal_horizons": [_public_horizon(item) for item in analysis.signal_horizons],
        "sparkline": downsample(displayed_series),
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
    view: str,
    horizon: int | None,
    admin: bool = False,
    wrapper_prompt: str = "",
) -> dict:
    summary = serialize_rebuilt_summary(
        analysis,
        arena,
        allocation_policy,
        view=view,
        horizon=horizon,
    )
    selected = summary["selected_policy"]
    policy = analysis.policies.get((selected["horizon"], selected["exposure_pct"])) if selected else None
    aggregate_policy = policy
    aggregate_policy_provisional = False
    if view == "common" and policy is None and analysis.portfolio.status == "active" and not analysis.error:
        aggregate_policy = analysis.policies.get(COMMON_INCUBATION_POLICY)
        aggregate_policy_provisional = aggregate_policy is not None
    recent_signals = sorted(analysis.portfolio.signals, key=lambda signal: signal.id, reverse=True)[:20]
    if view == "common":
        common = arena.common_for(analysis.portfolio.direction)
        series = common.member_series.get(analysis.portfolio.id, []) if policy else []
        spy_series = common.spy_series if policy else []
    else:
        series = policy.series if policy else []
        spy_series = policy.spy_series if policy else []
    return {
        **summary,
        "execution_prompt": manual_execution_prompt(
            analysis.portfolio,
            wrapper_prompt,
            direction_instructions,
            allocation_policy,
        ),
        "series": series,
        "spy_series": spy_series,
        "aggregate_policy": _aggregate_policy_payload(
            aggregate_policy,
            provisional=aggregate_policy_provisional,
        ),
        "holdings": aggregate_policy.holdings if aggregate_policy else [],
        "active_cohorts": aggregate_policy.active_cohorts if aggregate_policy else [],
        "signals": [serialize_signal(signal, admin=admin) for signal in recent_signals],
        "signals_next_cursor": (
            recent_signals[-1].id if len(analysis.portfolio.signals) > len(recent_signals) else None
        ),
        "policy_matrix": [
            {
                "horizon": pair[0],
                "exposure_pct": pair[1],
                "metrics": metrics,
            }
            for pair, metrics in sorted(analysis.policy_metrics.items())
        ],
    }
