"""Public, redacted analytics helpers for Arena-synthesis portfolios."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import MetaBatch, Portfolio
from .arena import RebuiltArena, RebuiltPortfolioAnalysis
from .harnesses import get_harness
from .meta_synthesis import source_counts
from .rebuilt import PolicyResult


def is_normal_portfolio(portfolio: Portfolio) -> bool:
    return portfolio.prompt.context_scope == "portfolio"


def is_meta_portfolio(portfolio: Portfolio) -> bool:
    return portfolio.prompt.context_scope == "arena"


def public_batches(session: Session) -> list[dict]:
    """Report each harness's progress independently for the latest scheduled session."""
    latest_date = select(func.max(MetaBatch.session_date)).scalar_subquery()
    batches = session.scalars(
        select(MetaBatch).where(MetaBatch.session_date == latest_date).order_by(MetaBatch.harness)
    ).all()
    result = []
    for batch in batches:
        harness = get_harness(batch.harness)
        result.append(
            {
                **_public_batch(batch),
                "harness": batch.harness,
                "harness_name": harness.name if harness is not None else batch.harness,
            }
        )
    return result


def _public_batch(batch: MetaBatch) -> dict:
    """Expose operational counts without exposing source identities or reasoning."""
    snapshot = batch.snapshot if isinstance(batch.snapshot, dict) else {}
    source_ids = set(batch.source_portfolio_ids)
    counts = (
        source_counts([source for source in snapshot["sources"] if source["portfolio"]["id"] in source_ids])
        if snapshot
        else {}
    )
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
