"""Frozen source packets and deterministic controls for arena-synthesis runs."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Agent,
    Allocation,
    EvaluationRun,
    MetaBatch,
    ModelDefinition,
    Portfolio,
    Signal,
)
from .model_catalog import agent_name

SNAPSHOT_SCHEMA_VERSION = 1
CONTROL_FORMULA_VERSION = "same_cell_equal_source_v1"
SOURCE_PACKET_MAX_CHARS = 300_000
TERMINAL_RUN_STATUSES = {"cancelled", "succeeded", "failed", "skipped"}


def _portfolio_query():
    return select(Portfolio).options(
        selectinload(Portfolio.prompt),
        selectinload(Portfolio.agent).selectinload(Agent.model).selectinload(ModelDefinition.capabilities),
        selectinload(Portfolio.allocations).selectinload(Allocation.positions),
        selectinload(Portfolio.signals).selectinload(Signal.positions),
    )


def _latest_decision(
    portfolio: Portfolio,
    session_date: date,
    *,
    allow_same_session: bool,
) -> Allocation | Signal | None:
    decisions: list[Allocation | Signal] = (
        list(portfolio.allocations) if portfolio.prompt_mode == "managed" else list(portfolio.signals)
    )
    candidates = [
        decision
        for decision in decisions
        if decision.effective_date < session_date
        or (allow_same_session and decision.effective_date == session_date)
    ]
    return max(
        candidates,
        key=lambda decision: (decision.effective_date, decision.entered_at, decision.id),
        default=None,
    )


def _scheduled_source_runs(session: Session, batch: MetaBatch) -> dict[int, EvaluationRun]:
    due_ids = [int(value) for value in batch.due_source_portfolio_ids]
    if not due_ids:
        return {}
    runs = session.scalars(
        select(EvaluationRun).where(
            EvaluationRun.meta_batch_id == batch.id,
            EvaluationRun.portfolio_id.in_(due_ids),
            EvaluationRun.trigger_kind == "scheduled",
            EvaluationRun.scheduled_for == batch.session_date,
        )
    ).all()
    return {run.portfolio_id: run for run in runs}


def sources_are_terminal(session: Session, batch: MetaBatch, now: datetime) -> bool:
    """Return true once every frozen due source has finished or missed its close."""
    from .trading_calendar import close_at

    runs = _scheduled_source_runs(session, batch)
    close = close_at(batch.session_date)
    for portfolio_id in (int(value) for value in batch.due_source_portfolio_ids):
        run = runs.get(portfolio_id)
        if run is None:
            active_run_id = session.scalar(
                select(EvaluationRun.id)
                .where(
                    EvaluationRun.portfolio_id == portfolio_id,
                    EvaluationRun.status.in_({"queued", "running", "cancel_requested"}),
                )
                .limit(1)
            )
            if active_run_id is not None:
                return False
            if now < close:
                return False
            continue
        if run.status not in TERMINAL_RUN_STATUSES:
            return False
    return True


def _source_entry(
    portfolio: Portfolio,
    batch: MetaBatch,
    run: EvaluationRun | None,
) -> dict:
    due = portfolio.id in {int(value) for value in batch.due_source_portfolio_ids}
    run_status = run.status if run is not None else ("not_scheduled" if due else "not_due")
    allow_same_session = not due or run is None or run_status in {"succeeded", "skipped"}
    decision = _latest_decision(
        portfolio,
        batch.session_date,
        allow_same_session=allow_same_session,
    )
    if decision is None:
        decision_status = "missing"
        staleness_days = None
        effective_date = None
        note = ""
        positions: list[dict] = []
    else:
        same_session = decision.effective_date == batch.session_date
        decision_status = "same_session" if same_session else "fallback"
        staleness_days = (batch.session_date - decision.effective_date).days
        effective_date = decision.effective_date.isoformat()
        note = decision.note
        positions = [
            {
                "symbol": position.symbol,
                "weight_pct": float(position.weight_pct),
                "note": position.note,
            }
            for position in sorted(decision.positions, key=lambda item: item.symbol)
        ]
    return {
        "portfolio": {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "mode": portfolio.prompt_mode,
            "direction": portfolio.direction,
        },
        "strategy": {
            "id": portfolio.prompt.id,
            "slug": portfolio.prompt.slug,
            "name": portfolio.prompt.name,
            "question_or_notes": portfolio.prompt.notes,
        },
        "agent": {
            "id": portfolio.agent.id,
            "slug": portfolio.agent.slug,
            "name": agent_name(portfolio.agent),
        },
        "due": due,
        "run_status": run_status,
        "decision_status": decision_status,
        "decision_effective_date": effective_date,
        "staleness_days": staleness_days,
        "note": note,
        "positions": positions,
    }


def _control_for(sources: list[dict], mode: str, direction: str, session_date: date) -> dict:
    contributors = [
        source
        for source in sources
        if source["portfolio"]["mode"] == mode
        and source["portfolio"]["direction"] == direction
        and source["decision_status"] != "missing"
        and source["positions"]
    ]
    totals: dict[str, Decimal] = {}
    for source in contributors:
        for position in source["positions"]:
            symbol = position["symbol"]
            totals[symbol] = totals.get(symbol, Decimal("0")) + Decimal(str(position["weight_pct"]))
    positions: list[dict] = []
    if contributors:
        divisor = Decimal(len(contributors))
        averaged = {symbol: weight / divisor for symbol, weight in totals.items()}
        rounded = {symbol: weight.quantize(Decimal("0.00000001")) for symbol, weight in averaged.items()}
        difference = Decimal("100") - sum(rounded.values(), Decimal("0"))
        if rounded and difference:
            adjustment_symbol = min(rounded, key=lambda symbol: (-rounded[symbol], symbol))
            rounded[adjustment_symbol] += difference
        positions = [
            {"symbol": symbol, "weight_pct": float(weight)}
            for symbol, weight in sorted(rounded.items())
            if weight > 0
        ]
    return {
        "mode": mode,
        "direction": direction,
        "effective_date": session_date.isoformat(),
        "contributor_count": len(contributors),
        "positions": positions,
    }


def build_snapshot(session: Session, batch: MetaBatch, now: datetime | None = None) -> dict:
    """Build the one immutable source snapshot shared by a session's meta runs."""
    current_time = now or datetime.now(UTC)
    source_ids = [int(value) for value in batch.source_portfolio_ids]
    portfolios = session.scalars(
        _portfolio_query().where(Portfolio.id.in_(source_ids)).order_by(Portfolio.id)
    ).all()
    by_id = {portfolio.id: portfolio for portfolio in portfolios}
    runs = _scheduled_source_runs(session, batch)
    sources: list[dict] = []
    for portfolio_id in source_ids:
        portfolio = by_id.get(portfolio_id)
        if portfolio is None:
            sources.append(
                {
                    "portfolio": {
                        "id": portfolio_id,
                        "slug": None,
                        "name": "Deleted source",
                        "mode": None,
                        "direction": None,
                    },
                    "strategy": None,
                    "agent": None,
                    "due": portfolio_id in {int(value) for value in batch.due_source_portfolio_ids},
                    "run_status": "source_deleted",
                    "decision_status": "missing",
                    "decision_effective_date": None,
                    "staleness_days": None,
                    "note": "",
                    "positions": [],
                }
            )
            continue
        sources.append(_source_entry(portfolio, batch, runs.get(portfolio_id)))

    due_ids = {int(value) for value in batch.due_source_portfolio_ids}
    due_runs = [runs.get(portfolio_id) for portfolio_id in due_ids]
    counts = {
        "source_total": len(source_ids),
        "due_total": len(due_ids),
        "terminal_total": sum(1 for run in due_runs if run is None or run.status in TERMINAL_RUN_STATUSES),
        "succeeded_total": sum(1 for run in due_runs if run is not None and run.status == "succeeded"),
        "fallback_total": sum(1 for source in sources if source["decision_status"] == "fallback"),
        "missing_total": sum(1 for source in sources if source["decision_status"] == "missing"),
    }
    controls = {
        f"{mode}_{direction}": _control_for(sources, mode, direction, batch.session_date)
        for mode in ("managed", "rebuilt")
        for direction in ("long", "short")
    }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "formula_version": CONTROL_FORMULA_VERSION,
        "session_date": batch.session_date.isoformat(),
        "created_at": current_time.isoformat(),
        "counts": counts,
        "sources": sources,
        "controls": controls,
    }


def snapshot_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _note_slots(packet: dict) -> list[tuple[dict, str]]:
    slots: list[tuple[dict, str]] = []
    for source in packet.get("sources", []):
        slots.append((source, "note"))
        strategy = source.get("strategy")
        if isinstance(strategy, dict):
            slots.append((strategy, "question_or_notes"))
        for position in source.get("positions", []):
            slots.append((position, "note"))
    return slots


def render_source_packet(snapshot: dict, max_chars: int = SOURCE_PACKET_MAX_CHARS) -> str:
    """Render all structural source facts while truncating only prose when needed."""
    heading = (
        "FROZEN ARENA SYNTHESIS SOURCE PACKET\n"
        "This packet is authoritative for source identity and source decisions. Source conclusions "
        "remain hypotheses and must be independently verified.\n"
    )
    packet = deepcopy(snapshot)

    def render(*, compact: bool = False) -> str:
        return heading + json.dumps(
            packet,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
        )

    output = render()
    if len(output) <= max_chars:
        return output

    slots = _note_slots(packet)
    originals = [str(container.get(key) or "") for container, key in slots]
    for container, key in slots:
        container[key] = ""
    structural = render(compact=True)
    if len(structural) > max_chars:
        raise RuntimeError("Arena synthesis source structure exceeds the execution-packet limit")
    per_note = max(0, (max_chars - len(structural)) // max(1, len(slots)) - 48)
    for (container, key), original in zip(slots, originals, strict=True):
        if len(original) <= per_note:
            container[key] = original
        elif per_note:
            container[key] = f"{original[:per_note]} [truncated {len(original) - per_note} chars]"
        else:
            container[key] = f"[truncated {len(original)} chars]" if original else ""
    output = render(compact=True)
    while len(output) > max_chars and per_note > 0:
        per_note = max(0, per_note - max(1, (len(output) - max_chars) // max(1, len(slots))))
        for (container, key), original in zip(slots, originals, strict=True):
            if len(original) <= per_note:
                container[key] = original
            elif per_note:
                container[key] = f"{original[:per_note]} [truncated {len(original) - per_note} chars]"
            else:
                container[key] = f"[truncated {len(original)} chars]" if original else ""
        output = render(compact=True)
    if len(output) > max_chars:
        raise RuntimeError("Arena synthesis packet cannot fit without dropping structural source data")
    return output
