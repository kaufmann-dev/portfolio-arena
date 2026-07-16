"""Admin write endpoints (JWT bearer). All experiment-integrity logic lives in
``services/admin_ops.py`` and is shared with the MCP tools; these handlers only
translate request bodies and ``AdminOpError`` into HTTP."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_session
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
from ..services import admin_ops, price_cache
from ..services.admin_ops import AdminOpError
from ..services.symbols import SymbolValidationError, resolve_symbol, search_symbols_allowed
from ..services.trading_calendar import effective_date_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _run[T](fn: Callable[..., T], *args, **kwargs) -> T:
    """Call an admin_ops function, mapping AdminOpError to an HTTP error."""
    try:
        return fn(*args, **kwargs)
    except AdminOpError as exc:
        raise HTTPException(exc.status_code, exc.message) from None


def _positions(body: AllocationCreate | AllocationUpdate) -> list[dict] | None:
    if body.positions is None:
        return None
    return [{"symbol": p.symbol, "weight_pct": p.weight_pct, "note": p.note} for p in body.positions]


# --- Agents -----------------------------------------------------------------


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, session: Session = Depends(get_session)):
    return _run(admin_ops.create_agent, session, name=body.name, slug=body.slug, notes=body.notes)


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: int, body: AgentPatch, session: Session = Depends(get_session)):
    return _run(admin_ops.update_agent, session, agent_id, name=body.name, notes=body.notes)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_agent, session, agent_id)


# --- Prompts ----------------------------------------------------------------


@router.post("/prompts", status_code=201)
def create_prompt(body: PromptCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_prompt,
        session,
        name=body.name,
        text=body.text,
        slug=body.slug,
        notes=body.notes,
        allocation_policy=body.allocation_policy.model_dump(),
    )


@router.patch("/prompts/{prompt_id}")
def patch_prompt(prompt_id: int, body: PromptPatch, session: Session = Depends(get_session)):
    return _run(
        admin_ops.update_prompt,
        session,
        prompt_id,
        name=body.name,
        text=body.text,
        notes=body.notes,
        allocation_policy=body.allocation_policy.model_dump() if body.allocation_policy else None,
    )


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_prompt, session, prompt_id)


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
        "security_type": resolved.security_type,
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


@router.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_portfolio,
        session,
        name=body.name,
        agent_id=body.agent_id,
        prompt_id=body.prompt_id,
        slug=body.slug,
        cost_bps=body.cost_bps,
    )


@router.patch("/portfolios/{portfolio_id}")
def patch_portfolio(portfolio_id: int, body: PortfolioPatch, session: Session = Depends(get_session)):
    return _run(
        admin_ops.update_portfolio,
        session,
        portfolio_id,
        name=body.name,
        status=body.status,
        agent_id=body.agent_id,
        prompt_id=body.prompt_id,
        cost_bps=body.cost_bps,
    )


@router.delete("/portfolios/{portfolio_id}")
def delete_portfolio(portfolio_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_portfolio, session, portfolio_id)


@router.get("/portfolios/{portfolio_id}/detail")
def portfolio_admin_detail(portfolio_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.portfolio_admin_detail, session, portfolio_id)


@router.get("/evaluation-runs")
def evaluation_runs(
    portfolio_id: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return _run(
        admin_ops.list_evaluation_runs,
        session,
        portfolio_id=portfolio_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )


@router.post("/portfolios/{portfolio_id}/allocations", status_code=201)
def create_allocation(portfolio_id: int, body: AllocationCreate, session: Session = Depends(get_session)):
    return _run(admin_ops.create_allocation, session, portfolio_id, _positions(body), body.note)


@router.put("/allocations/{allocation_id}")
def update_allocation(allocation_id: int, body: AllocationUpdate, session: Session = Depends(get_session)):
    return _run(admin_ops.update_allocation, session, allocation_id, _positions(body), body.note)


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_allocation, session, allocation_id)


# --- Settings & cache ---------------------------------------------------------


@router.get("/settings")
def get_app_settings(session: Session = Depends(get_session)):
    return admin_ops.get_default_cost_bps(session)


@router.put("/settings")
def put_app_settings(body: SettingsUpdate, session: Session = Depends(get_session)):
    return admin_ops.set_default_cost_bps(session, body.default_cost_bps)


@router.delete("/prices/cache")
def clear_price_cache(session: Session = Depends(get_session)):
    deleted = price_cache.clear_cache(session)
    return {"deleted": deleted}
