"""Write operations shared by the REST admin router and the MCP tools.

Every experiment-integrity rule lives here exactly once: server-set entry
times, computed effective dates (no backdating), position locking after the
effective close, benchmark protection, and slug uniqueness. Functions own their
`session.commit()` and raise `AdminOpError` on any rule violation; callers
translate that into their transport's error shape (HTTP status / tool error).
"""

import base64
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, Allocation, EvaluationRun, Portfolio, Position, Prompt, Setting
from ..seed import DEFAULT_COST_BPS_KEY
from ..util import slugify
from .arena import compute_valuations, load_portfolios
from .benchmarks import ensure_benchmark_allocations
from .prompt_policy import allocation_policy_out, validate_position_weights
from .serialize import serialize_allocation, serialize_detail
from .symbols import (
    SymbolValidationError,
    normalize_symbol,
    resolve_symbol,
    validate_positions,
)
from .trading_calendar import close_at, effective_date_for, is_locked, is_trading_day

DEFAULT_COST_BPS_FALLBACK = 10
EVALUATION_START_BEFORE_CLOSE = timedelta(minutes=90)
EVALUATION_CUTOFF_BEFORE_CLOSE = timedelta(minutes=10)
EVALUATION_LEASE = timedelta(minutes=30)
EVALUATION_MAX_ATTEMPTS = 2
EVALUATION_ERROR_MAX_LENGTH = 4000
EVALUATION_REPORT_MAX_LENGTH = 20_000


class AdminOpError(Exception):
    """A transport-neutral failure carrying an HTTP-style status code."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def unique_slug(session: Session, model, wanted: str) -> str:
    slug = slugify(wanted)
    candidate = slug
    suffix = 2
    while session.scalars(select(model).where(model.slug == candidate)).first() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


# --- Agents -----------------------------------------------------------------


def _agent_out(agent: Agent) -> dict:
    return {"id": agent.id, "slug": agent.slug, "name": agent.name, "notes": agent.notes}


def create_agent(session: Session, *, name: str, slug: str | None = None, notes: str = "") -> dict:
    agent = Agent(slug=unique_slug(session, Agent, slug or name), name=name, notes=notes)
    session.add(agent)
    session.commit()
    return _agent_out(agent)


def update_agent(
    session: Session, agent_id: int, *, name: str | None = None, notes: str | None = None
) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(404, "Agent not found")
    if name is not None:
        agent.name = name
    if notes is not None:
        agent.notes = notes
    session.commit()
    return _agent_out(agent)


def delete_agent(session: Session, agent_id: int) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(404, "Agent not found")
    if agent.slug == "benchmark":
        raise AdminOpError(403, "The benchmark agent is system-managed")
    count = session.scalar(select(func.count()).select_from(Portfolio).where(Portfolio.agent_id == agent_id))
    if count:
        raise AdminOpError(409, f"{count} portfolio(s) still use this agent — delete or reassign them first.")
    session.delete(agent)
    session.commit()
    return {"ok": True}


# --- Prompts ----------------------------------------------------------------


def prompt_out(prompt: Prompt) -> dict:
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "name": prompt.name,
        "text": prompt.text,
        "notes": prompt.notes,
        "allocation_policy": allocation_policy_out(prompt),
    }


def create_prompt(
    session: Session,
    *,
    name: str,
    text: str,
    allocation_policy: dict,
    slug: str | None = None,
    notes: str = "",
) -> dict:
    prompt = Prompt(
        slug=unique_slug(session, Prompt, slug or name),
        name=name,
        text=text,
        notes=notes,
        min_position_weight_pct=allocation_policy["min_position_weight_pct"],
        max_position_weight_pct=allocation_policy["max_position_weight_pct"],
    )
    session.add(prompt)
    session.commit()
    return prompt_out(prompt)


def update_prompt(
    session: Session,
    prompt_id: int,
    *,
    name: str | None = None,
    text: str | None = None,
    notes: str | None = None,
    allocation_policy: dict | None = None,
) -> dict:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise AdminOpError(404, "Prompt not found")
    if name is not None:
        prompt.name = name
    if text is not None:
        prompt.text = text
    if notes is not None:
        prompt.notes = notes
    if allocation_policy is not None:
        prompt.min_position_weight_pct = allocation_policy["min_position_weight_pct"]
        prompt.max_position_weight_pct = allocation_policy["max_position_weight_pct"]
    session.commit()
    return prompt_out(prompt)


def delete_prompt(session: Session, prompt_id: int) -> dict:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise AdminOpError(404, "Prompt not found")
    count = session.scalar(
        select(func.count()).select_from(Portfolio).where(Portfolio.prompt_id == prompt_id)
    )
    if count:
        raise AdminOpError(409, "This prompt is used by existing portfolios — it can't be deleted.")
    session.delete(prompt)
    session.commit()
    return {"ok": True}


# --- Portfolios -------------------------------------------------------------


def writable_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise AdminOpError(404, "Portfolio not found")
    if portfolio.is_benchmark:
        raise AdminOpError(403, "Benchmark portfolios are system-managed")
    return portfolio


def _default_cost_bps(session: Session) -> int:
    setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
    return int(setting.value) if setting else DEFAULT_COST_BPS_FALLBACK


def create_portfolio(
    session: Session,
    *,
    name: str,
    agent_id: int,
    prompt_id: int,
    slug: str | None = None,
    cost_bps: int | None = None,
) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(422, "Agent not found")
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise AdminOpError(422, "Prompt not found")

    portfolio = Portfolio(
        slug=unique_slug(session, Portfolio, slug or name),
        name=name,
        agent_id=agent.id,
        prompt_id=prompt.id,
        cost_bps=_default_cost_bps(session) if cost_bps is None else cost_bps,
    )
    session.add(portfolio)
    session.commit()
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "cost_bps": portfolio.cost_bps,
    }


def update_portfolio(
    session: Session,
    portfolio_id: int,
    *,
    name: str | None = None,
    status: str | None = None,
    agent_id: int | None = None,
    prompt_id: int | None = None,
    cost_bps: int | None = None,
) -> dict:
    portfolio = writable_portfolio(session, portfolio_id)
    if name is not None:
        portfolio.name = name
    if status is not None:
        portfolio.status = status
    if agent_id is not None:
        if session.get(Agent, agent_id) is None:
            raise AdminOpError(422, "Agent not found")
        portfolio.agent_id = agent_id
    if prompt_id is not None:
        if session.get(Prompt, prompt_id) is None:
            raise AdminOpError(422, "Prompt not found")
        portfolio.prompt_id = prompt_id
    if cost_bps is not None:
        portfolio.cost_bps = cost_bps
    session.commit()
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "status": portfolio.status,
        "agent_id": portfolio.agent_id,
        "prompt_id": portfolio.prompt_id,
        "cost_bps": portfolio.cost_bps,
    }


def delete_portfolio(session: Session, portfolio_id: int) -> dict:
    portfolio = writable_portfolio(session, portfolio_id)
    session.delete(portfolio)  # allocations + positions cascade
    session.commit()
    return {"ok": True}


def portfolio_admin_detail(session: Session, portfolio_id: int) -> dict:
    """Admin view: public detail plus the handoff fields (per-position notes,
    holding entry/current prices)."""
    ensure_benchmark_allocations(session)
    portfolios = load_portfolios(session)
    valuations = compute_valuations(session, portfolios)
    match = next((p for p in portfolios if p.id == portfolio_id), None)
    valuation = valuations.by_portfolio_id.get(match.id) if match else None
    if valuation is None:
        raise AdminOpError(404, "Portfolio not found")
    return {"as_of": valuations.as_of, "portfolio": serialize_detail(valuation, valuations, admin=True)}


# --- Allocations ------------------------------------------------------------


def _normalize_positions(prompt: Prompt, positions: list[dict]) -> list[dict]:
    """Normalize symbols and enforce the position-set rules (sum to 100, no dups,
    long-only) plus per-symbol resolution against Yahoo."""
    normalized = [
        {
            "symbol": normalize_symbol(p["symbol"]),
            "weight_pct": p["weight_pct"],
            "note": p.get("note", ""),
        }
        for p in positions
    ]
    try:
        validate_positions(normalized)
        for position in normalized:
            resolve_symbol(position["symbol"])
        validate_position_weights(prompt, normalized)
    except SymbolValidationError as exc:
        raise AdminOpError(422, exc.message) from None
    except ValueError as exc:
        raise AdminOpError(422, str(exc)) from None
    return normalized


def _apply_positions(allocation: Allocation, positions: list[dict]) -> None:
    for position in positions:
        allocation.positions.append(
            Position(
                symbol=position["symbol"],
                weight_pct=position["weight_pct"],
                note=position["note"],
            )
        )


def reload_allocation(session: Session, allocation_id: int) -> Allocation:
    return session.scalars(
        select(Allocation).where(Allocation.id == allocation_id).options(selectinload(Allocation.positions))
    ).one()


def _new_allocation(
    portfolio: Portfolio,
    positions: list[dict],
    note: str,
    entered_at: datetime,
    effective_date: date,
) -> Allocation:
    allocation = Allocation(
        portfolio_id=portfolio.id,
        entered_at=entered_at,
        effective_date=effective_date,
        note=note,
    )
    _apply_positions(allocation, positions)
    return allocation


def create_allocation(session: Session, portfolio_id: int, positions: list[dict], note: str = "") -> dict:
    """Enter a new allocation. Entry time is server-set; the effective date is the
    first market close strictly after it (no backdating). Rejects a clash if an
    allocation already takes effect that date — edit that one instead."""
    portfolio = writable_portfolio(session, portfolio_id)
    if portfolio.status != "active":
        raise AdminOpError(409, "Unarchive the portfolio before adding allocations")

    normalized = _normalize_positions(portfolio.prompt, positions)
    now = datetime.now(UTC)
    effective = effective_date_for(now)
    clash = session.scalars(
        select(Allocation).where(
            Allocation.portfolio_id == portfolio.id,
            Allocation.effective_date == effective,
        )
    ).first()
    if clash is not None:
        raise AdminOpError(
            409, f"An allocation already takes effect on {effective.isoformat()} — edit it instead."
        )

    allocation = _new_allocation(portfolio, normalized, note, now, effective)
    session.add(allocation)
    session.commit()
    return serialize_allocation(reload_allocation(session, allocation.id), admin=True)


def update_allocation(
    session: Session,
    allocation_id: int,
    positions: list[dict] | None = None,
    note: str | None = None,
) -> dict:
    """Positions are frozen once the effective close has passed; the note stays
    editable forever."""
    allocation = session.scalars(
        select(Allocation)
        .where(Allocation.id == allocation_id)
        .options(selectinload(Allocation.positions), selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise AdminOpError(404, "Allocation not found")
    if allocation.portfolio.is_benchmark:
        raise AdminOpError(403, "Benchmark portfolios are system-managed")

    if positions is not None:
        if is_locked(allocation.effective_date, datetime.now(UTC)):
            raise AdminOpError(
                403,
                "Positions are frozen: the effective close has passed. Enter a new rebalance instead.",
            )
        normalized = _normalize_positions(allocation.portfolio.prompt, positions)
        allocation.positions.clear()
        session.flush()  # delete old rows before inserting (unique on allocation+symbol)
        _apply_positions(allocation, normalized)

    if note is not None:
        allocation.note = note

    session.commit()
    return serialize_allocation(reload_allocation(session, allocation.id), admin=True)


def delete_allocation(session: Session, allocation_id: int) -> dict:
    allocation = session.scalars(
        select(Allocation).where(Allocation.id == allocation_id).options(selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise AdminOpError(404, "Allocation not found")
    if allocation.portfolio.is_benchmark:
        raise AdminOpError(403, "Benchmark portfolios are system-managed")
    if is_locked(allocation.effective_date, datetime.now(UTC)):
        raise AdminOpError(403, "This allocation is locked: its effective close has passed.")
    session.delete(allocation)
    session.commit()
    return {"ok": True}


# --- Automated evaluation runs ---------------------------------------------


def _evaluation_window(scheduled_for: date) -> tuple[datetime, datetime]:
    close = close_at(scheduled_for)
    return close - EVALUATION_START_BEFORE_CLOSE, close - EVALUATION_CUTOFF_BEFORE_CLOSE


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def evaluation_run_out(run: EvaluationRun) -> dict:
    return {
        "id": run.id,
        "portfolio": {
            "id": run.portfolio.id,
            "slug": run.portfolio.slug,
            "name": run.portfolio.name,
        },
        "agent": {
            "id": run.portfolio.agent.id,
            "slug": run.portfolio.agent.slug,
            "name": run.portfolio.agent.name,
        },
        "scheduled_for": run.scheduled_for.isoformat(),
        "model": run.model,
        "codex_version": run.codex_version,
        "status": run.status,
        "attempt_count": run.attempt_count,
        "lease_expires_at": _iso(run.lease_expires_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "allocation_id": run.allocation_id,
        "report": run.report,
        "error": run.error,
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
    }


def _run_query():
    return select(EvaluationRun).options(selectinload(EvaluationRun.portfolio).selectinload(Portfolio.agent))


def _load_evaluation_run(session: Session, run_id: int) -> EvaluationRun:
    run = session.scalars(_run_query().where(EvaluationRun.id == run_id)).first()
    if run is None:
        raise AdminOpError(404, "Evaluation run not found")
    return run


def begin_evaluation_run(
    session: Session,
    *,
    portfolio_slug: str,
    scheduled_for: date,
    model: str,
    codex_version: str,
    now: datetime | None = None,
) -> dict:
    """Acquire one bounded attempt for a portfolio's scheduled evaluation."""
    now = now or datetime.now(UTC)
    portfolio = session.scalars(
        select(Portfolio).where(Portfolio.slug == portfolio_slug).options(selectinload(Portfolio.agent))
    ).first()
    if portfolio is None:
        raise AdminOpError(404, f"Portfolio '{portfolio_slug}' not found")
    if portfolio.is_benchmark or portfolio.status != "active":
        raise AdminOpError(409, "Only active contestant portfolios can be evaluated")
    if not is_trading_day(scheduled_for):
        raise AdminOpError(422, f"{scheduled_for.isoformat()} is not a scheduled NYSE trading day")

    starts_at, deadline_at = _evaluation_window(scheduled_for)
    if now < starts_at:
        raise AdminOpError(409, f"Evaluation window opens at {starts_at.isoformat()}")
    if now >= deadline_at:
        raise AdminOpError(409, f"Evaluation cutoff passed at {deadline_at.isoformat()}")

    run = session.scalars(
        _run_query().where(
            EvaluationRun.portfolio_id == portfolio.id,
            EvaluationRun.scheduled_for == scheduled_for,
        )
    ).first()
    allocation = session.scalars(
        select(Allocation).where(
            Allocation.portfolio_id == portfolio.id,
            Allocation.effective_date == scheduled_for,
        )
    ).first()
    if allocation is not None:
        if run is None:
            run = EvaluationRun(
                portfolio=portfolio,
                scheduled_for=scheduled_for,
                model=model,
                codex_version=codex_version,
                status="skipped",
                attempt_count=0,
            )
            session.add(run)
        run.status = "skipped" if run.status != "succeeded" else run.status
        run.allocation_id = allocation.id
        run.lease_expires_at = None
        run.finished_at = run.finished_at or now
        session.commit()
        return {
            "action": "skip",
            "reason": "An allocation already exists for this session.",
            "deadline_at": deadline_at.isoformat(),
            "run": evaluation_run_out(_load_evaluation_run(session, run.id)),
        }

    if run is not None and run.status in {"succeeded", "skipped"}:
        return {
            "action": "skip",
            "reason": f"This session is already {run.status}.",
            "deadline_at": deadline_at.isoformat(),
            "run": evaluation_run_out(run),
        }
    if (
        run is not None
        and run.status == "running"
        and run.lease_expires_at is not None
        and run.lease_expires_at > now
    ):
        return {
            "action": "busy",
            "reason": "Another worker owns the active lease.",
            "deadline_at": deadline_at.isoformat(),
            "run": evaluation_run_out(run),
        }
    if run is not None and run.attempt_count >= EVALUATION_MAX_ATTEMPTS:
        return {
            "action": "exhausted",
            "reason": "The maximum number of attempts has been used.",
            "deadline_at": deadline_at.isoformat(),
            "run": evaluation_run_out(run),
        }

    if run is None:
        run = EvaluationRun(
            portfolio=portfolio,
            scheduled_for=scheduled_for,
            model=model,
            codex_version=codex_version,
            status="running",
            attempt_count=0,
        )
        session.add(run)
    run.model = model
    run.codex_version = codex_version
    run.status = "running"
    run.attempt_count += 1
    run.lease_expires_at = now + EVALUATION_LEASE
    run.started_at = now
    run.finished_at = None
    run.allocation_id = None
    run.report = None
    run.error = None
    session.commit()
    return {
        "action": "run",
        "deadline_at": deadline_at.isoformat(),
        "run": evaluation_run_out(_load_evaluation_run(session, run.id)),
    }


def submit_evaluation_allocation(
    session: Session,
    *,
    run_id: int,
    positions: list[dict],
    note: str,
    report: str,
    now: datetime | None = None,
) -> dict:
    """Atomically create the allocation and mark its evaluation successful."""
    now = now or datetime.now(UTC)
    run = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .options(
            selectinload(EvaluationRun.portfolio).selectinload(Portfolio.prompt),
            selectinload(EvaluationRun.portfolio).selectinload(Portfolio.agent),
        )
    ).first()
    if run is None:
        raise AdminOpError(404, "Evaluation run not found")
    if run.status == "succeeded" and run.allocation_id is not None:
        allocation = reload_allocation(session, run.allocation_id)
        return {"run": evaluation_run_out(run), "allocation": serialize_allocation(allocation, admin=True)}
    if run.status != "running":
        raise AdminOpError(409, f"Evaluation run is {run.status}, not running")
    if run.lease_expires_at is None or now >= run.lease_expires_at:
        raise AdminOpError(409, "Evaluation run lease expired")
    _, deadline_at = _evaluation_window(run.scheduled_for)
    if now >= deadline_at:
        raise AdminOpError(409, f"Evaluation cutoff passed at {deadline_at.isoformat()}")

    normalized = _normalize_positions(run.portfolio.prompt, positions)
    allocation = session.scalars(
        select(Allocation).where(
            Allocation.portfolio_id == run.portfolio_id,
            Allocation.effective_date == run.scheduled_for,
        )
    ).first()
    if allocation is None:
        allocation = _new_allocation(run.portfolio, normalized, note, now, run.scheduled_for)
        session.add(allocation)
        session.flush()
        run.status = "succeeded"
    else:
        run.status = "skipped"
    run.allocation_id = allocation.id
    run.report = report[:EVALUATION_REPORT_MAX_LENGTH]
    run.error = None
    run.lease_expires_at = None
    run.finished_at = now
    session.commit()
    return {
        "run": evaluation_run_out(_load_evaluation_run(session, run.id)),
        "allocation": serialize_allocation(reload_allocation(session, allocation.id), admin=True),
    }


def fail_evaluation_run(session: Session, *, run_id: int, error: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    run = _load_evaluation_run(session, run_id)
    if run.status in {"succeeded", "skipped"}:
        return evaluation_run_out(run)
    run.status = "failed"
    run.error = error[:EVALUATION_ERROR_MAX_LENGTH]
    run.lease_expires_at = None
    run.finished_at = now
    session.commit()
    return evaluation_run_out(_load_evaluation_run(session, run.id))


def _encode_run_cursor(run: EvaluationRun, portfolio_id: int | None, status: str | None) -> str:
    payload = json.dumps(
        {
            "date": run.scheduled_for.isoformat(),
            "id": run.id,
            "portfolio_id": portfolio_id,
            "status": status,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_run_cursor(cursor: str, portfolio_id: int | None, status: str | None) -> tuple[date, int]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if payload.get("portfolio_id") != portfolio_id or payload.get("status") != status:
            raise ValueError
        return date.fromisoformat(payload["date"]), int(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdminOpError(422, "Invalid evaluation-run cursor") from exc


def list_evaluation_runs(
    session: Session,
    *,
    portfolio_id: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    if status is not None and status not in {"running", "succeeded", "failed", "skipped"}:
        raise AdminOpError(422, "Invalid evaluation-run status")
    limit = max(1, min(limit, 100))
    query = _run_query()
    if portfolio_id is not None:
        query = query.where(EvaluationRun.portfolio_id == portfolio_id)
    if status is not None:
        query = query.where(EvaluationRun.status == status)
    if cursor is not None:
        cursor_date, cursor_id = _decode_run_cursor(cursor, portfolio_id, status)
        query = query.where(
            or_(
                EvaluationRun.scheduled_for < cursor_date,
                and_(
                    EvaluationRun.scheduled_for == cursor_date,
                    EvaluationRun.id < cursor_id,
                ),
            )
        )
    rows = session.scalars(
        query.order_by(EvaluationRun.scheduled_for.desc(), EvaluationRun.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [evaluation_run_out(run) for run in rows],
        "next_cursor": _encode_run_cursor(rows[-1], portfolio_id, status) if has_more and rows else None,
    }


# --- Settings ---------------------------------------------------------------


def get_default_cost_bps(session: Session) -> dict:
    return {"default_cost_bps": _default_cost_bps(session)}


def set_default_cost_bps(session: Session, value: int) -> dict:
    setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
    if setting is None:
        session.add(Setting(key=DEFAULT_COST_BPS_KEY, value=str(value)))
    else:
        setting.value = str(value)
    session.commit()
    return {"default_cost_bps": value}
