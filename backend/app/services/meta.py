"""Public, redacted analytics helpers for Arena-synthesis portfolios.

The evaluator owns construction of frozen MetaBatch snapshots.  This module
only reads their deterministic control decisions and values them from cached
market data; source notes and the raw snapshot never cross the public API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MetaBatch, Portfolio
from .arena import (
    ArenaValuations,
    PortfolioValuation,
    RebuiltArena,
    RebuiltPortfolioAnalysis,
    downsample,
)
from .rebuilt import PolicyResult, selected_objective_score

Track = Literal["managed", "rebuilt"]
Direction = Literal["long", "short"]
CONTROL_COST_BPS = 10
CONTROL_FORMULA_VERSION = "same_cell_equal_source_v1"


@dataclass
class ControlPosition:
    symbol: str
    weight_pct: float
    note: str = ""


@dataclass
class ControlDecision:
    id: int
    effective_date: date
    positions: list[ControlPosition]
    contributor_count: int = 0
    note: str = ""


@dataclass
class ControlPortfolio:
    """The minimum Portfolio-shaped input required by the analytics services."""

    id: int
    prompt_mode: Track
    direction: Direction
    allocations: list[ControlDecision] = field(default_factory=list)
    signals: list[ControlDecision] = field(default_factory=list)
    slug: str = ""
    name: str = "Consensus Control"
    status: str = "active"
    cost_bps: int = CONTROL_COST_BPS
    founding_v2: bool = True


def is_normal_portfolio(portfolio: Portfolio) -> bool:
    return portfolio.prompt.context_scope == "portfolio"


def is_meta_portfolio(portfolio: Portfolio) -> bool:
    return portfolio.prompt.context_scope == "arena"


def latest_batch(session: Session) -> MetaBatch | None:
    return session.scalar(select(MetaBatch).order_by(MetaBatch.session_date.desc(), MetaBatch.id.desc()))


def public_batch(batch: MetaBatch | None) -> dict | None:
    """Expose operational counts without exposing source identities or reasoning."""
    if batch is None:
        return None
    snapshot = batch.snapshot if isinstance(batch.snapshot, dict) else {}
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    return {
        "id": batch.id,
        "session_date": batch.session_date.isoformat(),
        "status": batch.status,
        "error": batch.error,
        "snapshot_sha256": batch.snapshot_sha256,
        "sources_finished_at": (
            batch.sources_finished_at.isoformat() if batch.sources_finished_at is not None else None
        ),
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
        "source_count": int(counts.get("source_total", len(batch.source_portfolio_ids or []))),
        "due_count": int(counts.get("due_total", len(batch.due_source_portfolio_ids or []))),
        "terminal_count": int(counts.get("terminal_total", 0)),
        "success_count": int(counts.get("succeeded_total", 0)),
        "fallback_count": int(counts.get("fallback_total", 0)),
        "missing_count": int(counts.get("missing_total", 0)),
        "target_count": len(batch.target_portfolio_ids or []),
    }


def _cell(track: Track, direction: Direction) -> str:
    return f"{track}_{direction}"


def _positions(raw: object) -> list[ControlPosition] | None:
    if not isinstance(raw, list) or not raw:
        return None
    by_symbol: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            return None
        symbol = str(item.get("symbol") or "").strip().upper()
        try:
            weight = float(item.get("weight_pct"))
        except (TypeError, ValueError):
            return None
        if not symbol or not math.isfinite(weight) or weight <= 0:
            return None
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + weight
    total = sum(by_symbol.values())
    if not math.isclose(total, 100.0, abs_tol=0.02):
        return None
    return [ControlPosition(symbol=symbol, weight_pct=weight) for symbol, weight in sorted(by_symbol.items())]


def control_history(
    session: Session,
    track: Track,
    direction: Direction,
) -> tuple[ControlPortfolio | None, str | None]:
    """Reconstruct one synthetic decision history from immutable ready batches."""
    batches = list(
        session.scalars(
            select(MetaBatch)
            .where(MetaBatch.status == "ready", MetaBatch.snapshot.is_not(None))
            .order_by(MetaBatch.session_date, MetaBatch.id)
        )
    )
    decisions_by_date: dict[date, ControlDecision] = {}
    latest_session: str | None = None
    key = _cell(track, direction)
    for batch in batches:
        snapshot = batch.snapshot if isinstance(batch.snapshot, dict) else {}
        controls = snapshot.get("controls")
        control = controls.get(key) if isinstance(controls, dict) else None
        if not isinstance(control, dict):
            continue
        if control.get("mode") not in (None, track) or control.get("direction") not in (
            None,
            direction,
        ):
            continue
        raw_date = control.get("effective_date") or batch.session_date.isoformat()
        try:
            effective_date = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        positions = _positions(control.get("positions"))
        if positions is None:
            continue
        try:
            contributor_count = max(0, int(control.get("contributor_count", 0)))
        except (TypeError, ValueError):
            contributor_count = 0
        decisions_by_date[effective_date] = ControlDecision(
            id=batch.id,
            effective_date=effective_date,
            positions=positions,
            contributor_count=contributor_count,
        )
        latest_session = batch.session_date.isoformat()

    if not decisions_by_date:
        return None, None
    decisions = [decisions_by_date[day] for day in sorted(decisions_by_date)]
    control_id = -1 if track == "managed" and direction == "long" else -2
    if track == "rebuilt":
        control_id -= 2
    if direction == "short":
        control_id -= 1
    portfolio = ControlPortfolio(
        id=control_id,
        prompt_mode=track,
        direction=direction,
        allocations=decisions if track == "managed" else [],
        signals=decisions if track == "rebuilt" else [],
        slug=f"consensus-control-{track}-{direction}",
    )
    return portfolio, latest_session


def _control_base(
    track: Track,
    direction: Direction,
    latest_session: str | None,
    metrics: dict,
    series: list[dict],
    *,
    error: str | None,
    stale_data: bool,
    frozen_symbols: list[str],
    liquidated_at: str | None,
    contributor_count: int,
) -> dict:
    return {
        "kind": "control",
        "id": None,
        "slug": f"consensus-control-{track}-{direction}",
        "name": "Consensus Control",
        "prompt_mode": track,
        "direction": direction,
        "status": "reference",
        "rank": None,
        "rank_score": None,
        "cost_bps": CONTROL_COST_BPS,
        "formula_version": CONTROL_FORMULA_VERSION,
        "batch_session_date": latest_session,
        "contributor_count": contributor_count,
        "evidence": metrics.get("evidence", "pending"),
        "metrics": metrics,
        "sparkline": downsample(series),
        "error": error,
        "stale_data": stale_data,
        "frozen_symbols": frozen_symbols,
        "is_liquidated": liquidated_at is not None,
        "liquidated_at": liquidated_at,
    }


def serialize_managed_control(
    valuation: PortfolioValuation | None,
    valuations: ArenaValuations,
    latest_session: str | None,
) -> dict | None:
    if valuation is None:
        return None
    result = valuation.result
    series = result.series if result else []
    payload = _control_base(
        "managed",
        valuation.portfolio.direction,
        latest_session,
        valuation.metrics,
        series,
        error=valuation.error,
        stale_data=bool(result and result.stale_days),
        frozen_symbols=result.frozen_symbols if result else [],
        liquidated_at=result.liquidated_at if result else None,
        contributor_count=(
            valuation.portfolio.allocations[-1].contributor_count if valuation.portfolio.allocations else 0
        ),
    )
    payload.update(
        {
            "inception": series[0]["date"] if series else None,
            "age_days": (
                (valuations.current_date - date.fromisoformat(series[0]["date"])).days if series else None
            ),
            "allocation_count": len(valuation.portfolio.allocations),
        }
    )
    return payload


def _pending_metrics() -> dict:
    return {
        "has_data": False,
        "eligible": False,
        "evidence": "pending",
        "ci_lower": None,
        "ci_upper": None,
    }


def rebuilt_display(
    analysis: RebuiltPortfolioAnalysis,
    arena: RebuiltArena,
    *,
    view: str,
    horizon: int | None,
) -> tuple[PolicyResult | None, dict, list[dict], dict]:
    """Select a rebuilt analysis view without serializing an ORM Portfolio."""
    common = arena.common_for(analysis.portfolio.direction)
    direct = None
    if view == "common":
        common_pair = common.policy
        admitted = analysis.portfolio.id in common.member_ids
        policy = (
            analysis.policies.get((common_pair["horizon"], common_pair["exposure_pct"]))
            if common_pair and admitted
            else None
        )
        metrics = common.member_metrics.get(analysis.portfolio.id, _pending_metrics())
        series = common.member_series.get(analysis.portfolio.id, []) if policy else []
    elif view == "tuned":
        policy = analysis.selected
        metrics = policy.metrics if policy else _pending_metrics()
        series = policy.series if policy else []
    elif view == "signal":
        direct = next(
            (item for item in analysis.signal_horizons if item["horizon"] == horizon),
            None,
        )
        policy = analysis.policies.get((horizon, 100)) if horizon is not None else None
        metrics = (
            {
                **{key: value for key, value in direct.items() if key != "completed_cohorts"},
                "has_data": bool(direct.get("complete_count") or direct.get("open_count")),
            }
            if direct is not None
            else policy.metrics
            if policy
            else _pending_metrics()
        )
        series = policy.series if policy else []
    else:
        raise ValueError(f"Unknown rebuilt view: {view}")

    completion_horizon = policy.horizon if policy else None
    selected_horizon = direct or next(
        (item for item in analysis.signal_horizons if item["horizon"] == completion_horizon),
        None,
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
    return policy, metrics, series, completion


def serialize_rebuilt_control(
    analysis: RebuiltPortfolioAnalysis | None,
    arena: RebuiltArena,
    latest_session: str | None,
    *,
    view: str,
    horizon: int | None,
) -> dict | None:
    if analysis is None:
        return None
    policy, metrics, series, completion = rebuilt_display(
        analysis,
        arena,
        view=view,
        horizon=horizon,
    )
    payload = _control_base(
        "rebuilt",
        analysis.portfolio.direction,
        latest_session,
        metrics,
        series,
        error=analysis.error,
        stale_data=analysis.stale_data,
        frozen_symbols=analysis.frozen_symbols,
        liquidated_at=policy.liquidated_at if policy else None,
        contributor_count=(
            analysis.portfolio.signals[-1].contributor_count if analysis.portfolio.signals else 0
        ),
    )
    payload.update(
        {
            "selected_policy": (
                {
                    "horizon": policy.horizon,
                    "exposure_pct": policy.exposure_pct,
                    "objective_score": selected_objective_score(metrics, arena.objective),
                }
                if policy
                else None
            ),
            "completion": completion,
            "signal_horizons": [
                {
                    **{key: value for key, value in item.items() if key != "completed_cohorts"},
                    "has_data": bool(item.get("complete_count") or item.get("open_count")),
                }
                for item in analysis.signal_horizons
            ],
            "common_admitted": (
                analysis.portfolio.id in arena.common_for(analysis.portfolio.direction).member_ids
            ),
        }
    )
    return payload


def control_series(control: dict | None, series: list[dict]) -> dict | None:
    if control is None or not series:
        return None
    return {
        "slug": control["slug"],
        "name": control["name"],
        "kind": "control",
        "series": series,
    }
