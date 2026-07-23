"""Write operations shared by the REST admin router and the MCP tools.

Every experiment-integrity rule lives here exactly once: server-set entry
times, computed effective dates (no backdating), position locking after the
effective close, benchmark protection, and slug uniqueness. Functions own their
`session.commit()` and raise `AdminOpError` on any rule violation; callers
translate that into their transport's error shape (HTTP status / tool error).
"""

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, Allocation, Portfolio, Position, Prompt, Setting
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
from .trading_calendar import effective_date_for, is_locked

DEFAULT_COST_BPS_FALLBACK = 10


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
