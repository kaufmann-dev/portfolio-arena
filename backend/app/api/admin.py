"""Admin endpoints (browser session).

Experiment and evaluator rules live in their respective services and are
shared with MCP tools; these handlers translate service errors into HTTP.
"""

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
    EvaluationRunsCreate,
    EvaluatorSettingsUpdate,
    ModelCreate,
    ModelPatch,
    PortfolioCreate,
    PortfolioEvaluatorConfigUpdate,
    PortfolioPatch,
    PromptCreate,
    PromptPatch,
    SettingsUpdate,
    SignalCreate,
    SignalUpdate,
)
from ..security import require_admin
from ..services import admin_ops, evaluator, price_cache
from ..services.admin_ops import AdminOpError
from ..services.harnesses import harnesses_out
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


def _positions(
    body: AllocationCreate | AllocationUpdate | SignalCreate | SignalUpdate,
) -> list[dict] | None:
    if body.positions is None:
        return None
    return [{"symbol": p.symbol, "weight_pct": p.weight_pct, "note": p.note} for p in body.positions]


# --- Models and agents ------------------------------------------------------


@router.get("/harnesses")
def list_harnesses():
    return harnesses_out()


@router.get("/models")
def list_models(session: Session = Depends(get_session)):
    return admin_ops.list_models(session)


@router.post("/models", status_code=201)
def create_model(body: ModelCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_model,
        session,
        name=body.name,
        slug=body.slug,
        notes=body.notes,
        capabilities=[capability.model_dump() for capability in body.capabilities],
    )


@router.patch("/models/{model_id}")
def patch_model(model_id: int, body: ModelPatch, session: Session = Depends(get_session)):
    return _run(
        admin_ops.update_model,
        session,
        model_id,
        name=body.name,
        notes=body.notes,
        capabilities=(
            [capability.model_dump() for capability in body.capabilities]
            if body.capabilities is not None
            else None
        ),
    )


@router.delete("/models/{model_id}")
def delete_model(model_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_model, session, model_id)


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_agent,
        session,
        model_id=body.model_id,
        harness=body.harness,
        reasoning_effort=body.reasoning_effort,
        slug=body.slug,
        notes=body.notes,
    )


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: int, body: AgentPatch, session: Session = Depends(get_session)):
    return _run(
        admin_ops.update_agent,
        session,
        agent_id,
        model_id=body.model_id,
        harness=body.harness,
        reasoning_effort=body.reasoning_effort,
        notes=body.notes,
    )


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_agent, session, agent_id)


# --- Prompts ----------------------------------------------------------------


@router.get("/admin/prompts")
def list_prompts(
    status: str | None = None,
    session: Session = Depends(get_session),
):
    return _run(admin_ops.list_prompts, session, status=status)


@router.post("/admin/prompts", status_code=201)
def create_prompt(body: PromptCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_prompt,
        session,
        name=body.name,
        mode=body.mode,
        managed_text=body.managed_text,
        rebuilt_text=body.rebuilt_text,
        slug=body.slug,
        notes=body.notes,
        allocation_policy=body.allocation_policy.model_dump(),
    )


@router.patch("/admin/prompts/{prompt_id}")
def patch_prompt(prompt_id: int, body: PromptPatch, session: Session = Depends(get_session)):
    return _run(
        admin_ops.update_prompt,
        session,
        prompt_id,
        name=body.name,
        mode=body.mode,
        managed_text=body.managed_text,
        rebuilt_text=body.rebuilt_text,
        notes=body.notes,
        allocation_policy=body.allocation_policy.model_dump() if body.allocation_policy else None,
    )


@router.post("/admin/prompts/{prompt_id}/archive")
def archive_prompt(prompt_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.archive_prompt, session, prompt_id)


@router.post("/admin/prompts/{prompt_id}/unarchive")
def unarchive_prompt(prompt_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.unarchive_prompt, session, prompt_id)


@router.get("/admin/prompts/{prompt_id}/versions")
def list_prompt_versions(prompt_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.list_prompt_versions, session, prompt_id)


@router.post(
    "/admin/prompts/{prompt_id}/versions/{version}/restore",
    status_code=201,
)
def restore_prompt_version(
    prompt_id: int,
    version: int,
    session: Session = Depends(get_session),
):
    return _run(
        admin_ops.restore_prompt_version,
        session,
        prompt_id,
        version,
    )


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
        prompt_mode=body.prompt_mode,
        direction=body.direction,
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
        prompt_mode=body.prompt_mode,
        direction=body.direction,
        cost_bps=body.cost_bps,
    )


@router.post("/portfolios/{portfolio_id}/reset")
def reset_portfolio(portfolio_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.reset_portfolio, session, portfolio_id)


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
        evaluator.list_runs,
        session,
        portfolio_id=portfolio_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )


@router.get("/evaluator")
def evaluator_dashboard(session: Session = Depends(get_session)):
    return evaluator.get_dashboard(session)


@router.put("/evaluator/settings")
def put_evaluator_settings(
    body: EvaluatorSettingsUpdate,
    session: Session = Depends(get_session),
):
    return _run(evaluator.update_settings, session, **body.model_dump())


@router.put("/evaluator/portfolios/{portfolio_id}")
def put_portfolio_evaluator_config(
    portfolio_id: int,
    body: PortfolioEvaluatorConfigUpdate,
    session: Session = Depends(get_session),
):
    return _run(
        evaluator.update_portfolio_config,
        session,
        portfolio_id=portfolio_id,
        **body.model_dump(),
    )


@router.post("/evaluator/runs", status_code=201)
def create_evaluator_runs(
    body: EvaluationRunsCreate,
    session: Session = Depends(get_session),
):
    return _run(evaluator.enqueue_manual_runs, session, portfolio_ids=body.portfolio_ids)


@router.post("/evaluator/runs/{run_id}/cancel")
def cancel_evaluator_run(run_id: int, session: Session = Depends(get_session)):
    return _run(evaluator.cancel_run, session, run_id=run_id)


@router.post("/evaluator/runs/{run_id}/retry", status_code=201)
def retry_evaluator_run(run_id: int, session: Session = Depends(get_session)):
    return _run(evaluator.retry_run, session, run_id=run_id)


@router.post("/portfolios/{portfolio_id}/allocations", status_code=201)
def create_allocation(portfolio_id: int, body: AllocationCreate, session: Session = Depends(get_session)):
    return _run(admin_ops.create_allocation, session, portfolio_id, _positions(body), body.note)


@router.put("/allocations/{allocation_id}")
def update_allocation(allocation_id: int, body: AllocationUpdate, session: Session = Depends(get_session)):
    return _run(admin_ops.update_allocation, session, allocation_id, _positions(body), body.note)


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_allocation, session, allocation_id)


@router.post("/portfolios/{portfolio_id}/signals", status_code=201)
def create_signal(portfolio_id: int, body: SignalCreate, session: Session = Depends(get_session)):
    return _run(
        admin_ops.create_signal,
        session,
        portfolio_id,
        _positions(body),
        body.note,
        provenance="browser_admin",
    )


@router.put("/signals/{signal_id}")
def update_signal(signal_id: int, body: SignalUpdate, session: Session = Depends(get_session)):
    return _run(admin_ops.update_signal, session, signal_id, _positions(body), body.note)


@router.delete("/signals/{signal_id}")
def delete_signal(signal_id: int, session: Session = Depends(get_session)):
    return _run(admin_ops.delete_signal, session, signal_id)


# --- Settings & cache ---------------------------------------------------------


@router.get("/settings")
def get_app_settings(session: Session = Depends(get_session)):
    return admin_ops.get_app_settings(session)


@router.put("/settings")
def put_app_settings(body: SettingsUpdate, session: Session = Depends(get_session)):
    return _run(admin_ops.update_app_settings, session, **body.model_dump())


@router.delete("/prices/cache")
def clear_price_cache(session: Session = Depends(get_session)):
    deleted = price_cache.clear_cache(session)
    return {"deleted": deleted}
