"""Admin write endpoints (JWT bearer). Experiment-integrity rules are enforced
here: server-set entry times, computed effective dates, and position locking
once the effective close has occurred."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import Agent, Allocation, Portfolio, Position, Prompt, Setting
from ..schemas import (
    AgentCreate,
    AgentPatch,
    AllocationCreate,
    AllocationUpdate,
    PortfolioCreate,
    PortfolioPatch,
    PromptCreate,
    PromptPatch,
    SettingsUpdate,
)
from ..security import require_admin
from ..seed import DEFAULT_COST_BPS_KEY
from ..services import price_cache
from ..services.arena import compute_valuations, load_portfolios
from ..services.benchmarks import ensure_benchmark_allocations
from ..services.symbols import (
    SymbolValidationError,
    derive_instrument,
    normalize_symbol,
    resolve_symbol,
    search_symbols_allowed,
    validate_positions,
)
from ..services.trading_calendar import effective_date_for, is_locked
from ..util import slugify
from .serialize import serialize_allocation, serialize_detail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _unique_slug(session: Session, model, wanted: str) -> str:
    slug = slugify(wanted)
    candidate = slug
    suffix = 2
    while session.scalars(select(model).where(model.slug == candidate)).first() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


# --- Agents -----------------------------------------------------------------


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, session: Session = Depends(get_session)):
    slug = _unique_slug(session, Agent, body.slug or body.name)
    agent = Agent(slug=slug, name=body.name, notes=body.notes)
    session.add(agent)
    session.commit()
    return {"id": agent.id, "slug": agent.slug, "name": agent.name, "notes": agent.notes}


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: int, body: AgentPatch, session: Session = Depends(get_session)):
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if body.name is not None:
        agent.name = body.name
    if body.notes is not None:
        agent.notes = body.notes
    session.commit()
    return {"id": agent.id, "slug": agent.slug, "name": agent.name, "notes": agent.notes}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int, session: Session = Depends(get_session)):
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if agent.slug == "benchmark":
        raise HTTPException(403, "The benchmark agent is system-managed")
    count = session.scalar(
        select(func.count()).select_from(Portfolio).where(Portfolio.agent_id == agent_id)
    )
    if count:
        raise HTTPException(
            409, f"{count} portfolio(s) still use this agent — delete or reassign them first."
        )
    session.delete(agent)
    session.commit()
    return {"ok": True}


# --- Prompts ----------------------------------------------------------------


def _prompt_out(prompt: Prompt) -> dict:
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "name": prompt.name,
        "text": prompt.text,
        "notes": prompt.notes,
    }


@router.post("/prompts", status_code=201)
def create_prompt(body: PromptCreate, session: Session = Depends(get_session)):
    slug = _unique_slug(session, Prompt, body.slug or body.name)
    prompt = Prompt(slug=slug, name=body.name, text=body.text, notes=body.notes)
    session.add(prompt)
    session.commit()
    return _prompt_out(prompt)


@router.patch("/prompts/{prompt_id}")
def patch_prompt(prompt_id: int, body: PromptPatch, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise HTTPException(404, "Prompt not found")
    if body.name is not None:
        prompt.name = body.name
    if body.text is not None:
        prompt.text = body.text
    if body.notes is not None:
        prompt.notes = body.notes
    session.commit()
    return _prompt_out(prompt)


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise HTTPException(404, "Prompt not found")
    count = session.scalar(
        select(func.count()).select_from(Allocation).where(Allocation.prompt_id == prompt_id)
    )
    if count:
        raise HTTPException(409, "This prompt is used by existing allocations — it can't be deleted.")
    session.delete(prompt)
    session.commit()
    return {"ok": True}


# --- Symbol validation (entry-form support) ----------------------------------


@router.get("/symbols/search")
def symbol_search(q: str):
    return {"results": search_symbols_allowed(q)}


@router.get("/symbols/{symbol}")
def symbol_resolution(symbol: str):
    try:
        resolved = resolve_symbol(symbol)
    except SymbolValidationError as exc:
        raise HTTPException(422, exc.message) from None
    return {
        "symbol": resolved.symbol,
        "instrument": resolved.instrument,
        "name": resolved.name,
        "currency": resolved.currency,
        "exchange": resolved.exchange,
    }


@router.get("/effective-date")
def effective_date_preview():
    """What the entry form shows before submitting."""
    now = datetime.now(UTC)
    return {"entered_at": now.isoformat(), "effective_date": effective_date_for(now).isoformat()}


# --- Portfolios & allocations -------------------------------------------------


def _writable_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(404, "Portfolio not found")
    if portfolio.is_benchmark:
        raise HTTPException(403, "Benchmark portfolios are system-managed")
    return portfolio


def _build_allocation(session: Session, portfolio: Portfolio, body: AllocationCreate) -> Allocation:
    """Validate + construct an allocation. Entry time is server-set; the
    effective date is the first market close strictly after it (no backdating)."""
    prompt = session.get(Prompt, body.prompt_id)
    if prompt is None:
        raise HTTPException(422, "Prompt not found")

    positions = [
        {"symbol": normalize_symbol(p.symbol), "weight_pct": p.weight_pct, "note": p.note}
        for p in body.positions
    ]
    try:
        validate_positions(positions)
        for position in positions:
            resolve_symbol(position["symbol"])
    except SymbolValidationError as exc:
        raise HTTPException(422, exc.message) from None

    now = datetime.now(UTC)
    effective = effective_date_for(now)
    clash = session.scalars(
        select(Allocation).where(
            Allocation.portfolio_id == portfolio.id,
            Allocation.effective_date == effective,
        )
    ).first()
    if clash is not None:
        raise HTTPException(
            409,
            f"An allocation already takes effect on {effective.isoformat()} — edit it instead.",
        )

    allocation = Allocation(
        portfolio_id=portfolio.id,
        prompt_id=prompt.id,
        entered_at=now,
        effective_date=effective,
        raw_response=body.raw_response,
        note=body.note,
    )
    for position in positions:
        allocation.positions.append(
            Position(
                symbol=position["symbol"],
                instrument=derive_instrument(position["symbol"]),
                weight_pct=position["weight_pct"],
                note=position["note"],
            )
        )
    return allocation


@router.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioCreate, session: Session = Depends(get_session)):
    agent = session.get(Agent, body.agent_id)
    if agent is None:
        raise HTTPException(422, "Agent not found")

    cost_bps = body.cost_bps
    if cost_bps is None:
        setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
        cost_bps = int(setting.value) if setting else 10

    portfolio = Portfolio(
        slug=_unique_slug(session, Portfolio, body.slug or body.name),
        name=body.name,
        agent_id=agent.id,
        cost_bps=cost_bps,
    )
    session.add(portfolio)
    session.flush()

    allocation = _build_allocation(session, portfolio, body.allocation)
    session.add(allocation)
    session.commit()
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "cost_bps": portfolio.cost_bps,
        "allocation": serialize_allocation(_reload_allocation(session, allocation.id), admin=True),
    }


@router.patch("/portfolios/{portfolio_id}")
def patch_portfolio(portfolio_id: int, body: PortfolioPatch, session: Session = Depends(get_session)):
    portfolio = _writable_portfolio(session, portfolio_id)
    if body.name is not None:
        portfolio.name = body.name
    if body.status is not None:
        portfolio.status = body.status
    if body.agent_id is not None:
        if session.get(Agent, body.agent_id) is None:
            raise HTTPException(422, "Agent not found")
        portfolio.agent_id = body.agent_id
    if body.cost_bps is not None:
        portfolio.cost_bps = body.cost_bps
    session.commit()
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "status": portfolio.status,
        "agent_id": portfolio.agent_id,
        "cost_bps": portfolio.cost_bps,
    }


@router.delete("/portfolios/{portfolio_id}")
def delete_portfolio(portfolio_id: int, session: Session = Depends(get_session)):
    portfolio = _writable_portfolio(session, portfolio_id)
    session.delete(portfolio)  # allocations + positions cascade
    session.commit()
    return {"ok": True}


@router.get("/portfolios/{portfolio_id}/detail")
def portfolio_admin_detail(portfolio_id: int, session: Session = Depends(get_session)):
    """Admin view of a portfolio: same shape as the public detail plus the
    admin-only handoff fields (per-position notes, holding entry/current prices)."""
    ensure_benchmark_allocations(session)
    portfolios = load_portfolios(session)
    valuations = compute_valuations(session, portfolios)
    match = next((p for p in portfolios if p.id == portfolio_id), None)
    valuation = valuations.by_portfolio_id.get(match.id) if match else None
    if valuation is None:
        raise HTTPException(404, "Portfolio not found")
    return {"as_of": valuations.as_of, "portfolio": serialize_detail(valuation, valuations, admin=True)}


def _reload_allocation(session: Session, allocation_id: int) -> Allocation:
    return session.scalars(
        select(Allocation)
        .where(Allocation.id == allocation_id)
        .options(selectinload(Allocation.positions), selectinload(Allocation.prompt))
    ).one()


@router.post("/portfolios/{portfolio_id}/allocations", status_code=201)
def create_allocation(portfolio_id: int, body: AllocationCreate, session: Session = Depends(get_session)):
    portfolio = _writable_portfolio(session, portfolio_id)
    if portfolio.status != "active":
        raise HTTPException(409, "Unarchive the portfolio before adding allocations")
    allocation = _build_allocation(session, portfolio, body)
    session.add(allocation)
    session.commit()
    return serialize_allocation(_reload_allocation(session, allocation.id), admin=True)


@router.put("/allocations/{allocation_id}")
def update_allocation(allocation_id: int, body: AllocationUpdate, session: Session = Depends(get_session)):
    allocation = session.scalars(
        select(Allocation)
        .where(Allocation.id == allocation_id)
        .options(selectinload(Allocation.positions), selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise HTTPException(404, "Allocation not found")
    if allocation.portfolio.is_benchmark:
        raise HTTPException(403, "Benchmark portfolios are system-managed")

    now = datetime.now(UTC)
    if body.positions is not None:
        if is_locked(allocation.effective_date, now):
            raise HTTPException(
                403,
                "Positions are frozen: the effective close has passed. Enter a new rebalance instead.",
            )
        positions = [
            {"symbol": normalize_symbol(p.symbol), "weight_pct": p.weight_pct, "note": p.note}
            for p in body.positions
        ]
        try:
            validate_positions(positions)
            for position in positions:
                resolve_symbol(position["symbol"])
        except SymbolValidationError as exc:
            raise HTTPException(422, exc.message) from None
        allocation.positions.clear()
        session.flush()  # delete old rows before inserting (unique on allocation+symbol)
        for position in positions:
            allocation.positions.append(
                Position(
                    symbol=position["symbol"],
                    instrument=derive_instrument(position["symbol"]),
                    weight_pct=position["weight_pct"],
                    note=position["note"],
                )
            )

    if body.prompt_id is not None:
        if session.get(Prompt, body.prompt_id) is None:
            raise HTTPException(422, "Prompt not found")
        allocation.prompt_id = body.prompt_id
    if body.raw_response is not None:
        allocation.raw_response = body.raw_response
    if body.note is not None:
        allocation.note = body.note

    session.commit()
    return serialize_allocation(_reload_allocation(session, allocation.id), admin=True)


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, session: Session = Depends(get_session)):
    allocation = session.scalars(
        select(Allocation).where(Allocation.id == allocation_id).options(selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise HTTPException(404, "Allocation not found")
    if allocation.portfolio.is_benchmark:
        raise HTTPException(403, "Benchmark portfolios are system-managed")
    if is_locked(allocation.effective_date, datetime.now(UTC)):
        raise HTTPException(403, "This allocation is locked: its effective close has passed.")
    session.delete(allocation)
    session.commit()
    return {"ok": True}


# --- Settings & cache ---------------------------------------------------------


@router.get("/settings")
def get_app_settings(session: Session = Depends(get_session)):
    setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
    return {"default_cost_bps": int(setting.value) if setting else 10}


@router.put("/settings")
def put_app_settings(body: SettingsUpdate, session: Session = Depends(get_session)):
    setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
    if setting is None:
        session.add(Setting(key=DEFAULT_COST_BPS_KEY, value=str(body.default_cost_bps)))
    else:
        setting.value = str(body.default_cost_bps)
    session.commit()
    return {"default_cost_bps": body.default_cost_bps}


@router.delete("/prices/cache")
def clear_price_cache(session: Session = Depends(get_session)):
    deleted = price_cache.clear_cache(session)
    return {"deleted": deleted}
