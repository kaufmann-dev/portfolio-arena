"""Write operations shared by the REST admin router and the MCP tools.

Every experiment-integrity rule lives here exactly once: server-set entry
times, computed effective dates (no backdating), position locking after the
effective close, benchmark protection, and slug uniqueness. Functions own their
`session.commit()` and raise `AdminOpError` on any rule violation; callers
translate that into their transport's error shape (HTTP status / tool error).
"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import BENCHMARK_IDENTITY, BENCHMARK_STRATEGY
from ..models import (
    Agent,
    Allocation,
    EvaluationRun,
    EvaluatorSettings,
    ModelDefinition,
    ModelHarnessCapability,
    Portfolio,
    PortfolioEvaluatorConfig,
    Position,
    Prompt,
    Setting,
)
from ..seed import DEFAULT_COST_BPS_KEY
from ..util import slugify
from .arena import compute_valuations, load_portfolios
from .benchmarks import reconcile_benchmark_allocations
from .errors import AdminOpError
from .harnesses import supports_automation
from .model_catalog import (
    agent_name,
    agent_out,
    execution_profile_name,
    load_agent,
    model_out,
    validate_agent_profile,
    validate_capabilities,
)
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


def unique_slug(session: Session, model, wanted: str) -> str:
    slug = slugify(wanted)
    candidate = slug
    suffix = 2
    while session.scalars(select(model).where(model.slug == candidate)).first() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


# --- Models and agents ------------------------------------------------------


def list_models(session: Session) -> dict:
    models = session.scalars(
        select(ModelDefinition)
        .options(selectinload(ModelDefinition.capabilities))
        .order_by(ModelDefinition.slug)
    ).all()
    counts = dict(session.execute(select(Agent.model_id, func.count()).group_by(Agent.model_id)).all())
    return {"models": [model_out(model, agent_count=counts.get(model.id, 0)) for model in models]}


def create_model(
    session: Session,
    *,
    name: str,
    capabilities: list[dict],
    slug: str | None = None,
    notes: str = "",
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise AdminOpError(422, "Model name is required")
    reserved_name = clean_name.casefold() == BENCHMARK_IDENTITY["name"].casefold()
    reserved_slug = slug is not None and slugify(slug) == BENCHMARK_IDENTITY["slug"]
    if reserved_name or reserved_slug:
        raise AdminOpError(409, "Benchmark is reserved for hardcoded benchmark portfolios")
    model = ModelDefinition(
        slug=unique_slug(session, ModelDefinition, slug or clean_name),
        name=clean_name,
        notes=notes,
    )
    for capability in validate_capabilities(capabilities):
        model.capabilities.append(ModelHarnessCapability(**capability))
    session.add(model)
    session.commit()
    return model_out(model, agent_count=0)


def update_model(
    session: Session,
    model_id: int,
    *,
    name: str | None = None,
    notes: str | None = None,
    capabilities: list[dict] | None = None,
) -> dict:
    model = session.scalars(
        select(ModelDefinition)
        .where(ModelDefinition.id == model_id)
        .options(selectinload(ModelDefinition.capabilities))
        .with_for_update()
    ).first()
    if model is None:
        raise AdminOpError(404, "Model not found")
    if capabilities is not None:
        normalized = validate_capabilities(capabilities)
        wanted = {item["harness"]: item for item in normalized}
        agents = session.scalars(select(Agent).where(Agent.model_id == model_id)).all()
        for agent in agents:
            if agent.harness is None:
                continue
            replacement = wanted.get(agent.harness)
            if replacement is None:
                raise AdminOpError(
                    409,
                    f"{agent_name(agent)} still uses the {agent.harness} capability",
                )
            if agent.reasoning_effort not in replacement["reasoning_efforts"]:
                if not (agent.reasoning_effort is None and not replacement["reasoning_efforts"]):
                    raise AdminOpError(
                        409,
                        f"{agent_name(agent)} still uses reasoning effort "
                        f"{agent.reasoning_effort or '(none)'}",
                    )
        existing = {item.harness: item for item in model.capabilities}
        for harness, item in wanted.items():
            capability = existing.get(harness)
            if capability is None:
                model.capabilities.append(ModelHarnessCapability(**item))
            else:
                capability.execution_model_id = item["execution_model_id"]
                capability.reasoning_efforts = item["reasoning_efforts"]
        for harness, capability in existing.items():
            if harness not in wanted:
                model.capabilities.remove(capability)
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise AdminOpError(422, "Model name is required")
        if clean_name.casefold() == BENCHMARK_IDENTITY["name"].casefold():
            raise AdminOpError(409, "Benchmark is reserved for hardcoded benchmark portfolios")
        model.name = clean_name
    if notes is not None:
        model.notes = notes
    session.commit()
    count = session.scalar(select(func.count()).select_from(Agent).where(Agent.model_id == model.id))
    return model_out(model, agent_count=count or 0)


def delete_model(session: Session, model_id: int) -> dict:
    model = session.get(ModelDefinition, model_id)
    if model is None:
        raise AdminOpError(404, "Model not found")
    agent_count = session.scalar(select(func.count()).select_from(Agent).where(Agent.model_id == model_id))
    run_count = session.scalar(
        select(func.count()).select_from(EvaluationRun).where(EvaluationRun.model_id == model_id)
    )
    if agent_count or run_count:
        raise AdminOpError(409, "This model is still used by an agent or evaluation run")
    session.delete(model)
    session.commit()
    return {"ok": True}


def _profile_exists(
    session: Session,
    *,
    model_id: int,
    harness: str | None,
    reasoning_effort: str | None,
    exclude_agent_id: int | None = None,
) -> bool:
    query = select(Agent.id).where(
        Agent.model_id == model_id,
        Agent.harness.is_(None) if harness is None else Agent.harness == harness,
        Agent.reasoning_effort.is_(None)
        if reasoning_effort is None
        else Agent.reasoning_effort == reasoning_effort,
    )
    if exclude_agent_id is not None:
        query = query.where(Agent.id != exclude_agent_id)
    return session.scalar(query.limit(1)) is not None


def create_agent(
    session: Session,
    *,
    model_id: int,
    harness: str | None,
    reasoning_effort: str | None,
    slug: str | None = None,
    notes: str = "",
) -> dict:
    model, _, clean_effort = validate_agent_profile(
        session,
        model_id=model_id,
        harness=harness,
        reasoning_effort=reasoning_effort,
    )
    clean_harness = harness.strip() if harness else None
    if slug is not None and slugify(slug) == BENCHMARK_IDENTITY["slug"]:
        raise AdminOpError(409, "Benchmark is reserved for hardcoded benchmark portfolios")
    if _profile_exists(
        session,
        model_id=model.id,
        harness=clean_harness,
        reasoning_effort=clean_effort,
    ):
        raise AdminOpError(409, "An agent with this execution profile already exists")
    agent = Agent(
        slug=unique_slug(
            session,
            Agent,
            slug or execution_profile_name(model, clean_harness, clean_effort),
        ),
        model_id=model.id,
        harness=clean_harness,
        reasoning_effort=clean_effort,
        notes=notes,
    )
    session.add(agent)
    session.commit()
    return agent_out(agent)


def update_agent(
    session: Session,
    agent_id: int,
    *,
    model_id: int | None = None,
    harness: str | None = None,
    reasoning_effort: str | None = None,
    notes: str | None = None,
) -> dict:
    agent = load_agent(session, agent_id)
    if agent is None:
        raise AdminOpError(404, "Agent not found")
    target_model_id = model_id if model_id is not None else agent.model_id
    model, _, clean_effort = validate_agent_profile(
        session,
        model_id=target_model_id,
        harness=harness,
        reasoning_effort=reasoning_effort,
    )
    clean_harness = harness.strip() if harness else None
    if _profile_exists(
        session,
        model_id=model.id,
        harness=clean_harness,
        reasoning_effort=clean_effort,
        exclude_agent_id=agent.id,
    ):
        raise AdminOpError(409, "An agent with this execution profile already exists")
    previous_harness = agent.harness
    agent.model = model
    agent.harness = clean_harness
    agent.reasoning_effort = clean_effort
    if notes is not None:
        agent.notes = notes
    if supports_automation(previous_harness) and not supports_automation(clean_harness):
        portfolio_ids = session.scalars(select(Portfolio.id).where(Portfolio.agent_id == agent.id)).all()
        _disable_portfolio_automation(
            session,
            portfolio_ids,
            "Cancelled because the agent no longer supports integrated automation.",
        )
    session.commit()
    return agent_out(agent)


def delete_agent(session: Session, agent_id: int) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(404, "Agent not found")
    count = session.scalar(select(func.count()).select_from(Portfolio).where(Portfolio.agent_id == agent_id))
    if count:
        raise AdminOpError(409, f"{count} portfolio(s) still use this agent — delete or reassign them first.")
    run_count = session.scalar(
        select(func.count()).select_from(EvaluationRun).where(EvaluationRun.agent_id == agent_id)
    )
    if run_count:
        raise AdminOpError(409, f"{run_count} evaluation run(s) still reference this agent.")
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
    reserved_name = name.strip().casefold() == BENCHMARK_STRATEGY["name"].casefold()
    reserved_slug = slug is not None and slugify(slug) == BENCHMARK_STRATEGY["slug"]
    if reserved_name or reserved_slug:
        raise AdminOpError(409, "Buy & Hold is reserved for hardcoded benchmark portfolios")
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
        if name.strip().casefold() == BENCHMARK_STRATEGY["name"].casefold():
            raise AdminOpError(409, "Buy & Hold is reserved for hardcoded benchmark portfolios")
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


def writable_portfolio(session: Session, portfolio_id: int, *, lock: bool = False) -> Portfolio:
    query = select(Portfolio).where(Portfolio.id == portfolio_id)
    if lock:
        query = query.with_for_update()
    portfolio = session.scalars(query).first()
    if portfolio is None:
        raise AdminOpError(404, "Portfolio not found")
    if portfolio.is_benchmark:
        raise AdminOpError(403, "Benchmark portfolios are system-managed")
    return portfolio


def _default_cost_bps(session: Session) -> int:
    setting = session.get(Setting, DEFAULT_COST_BPS_KEY)
    return int(setting.value) if setting else DEFAULT_COST_BPS_FALLBACK


def _disable_portfolio_automation(
    session: Session,
    portfolio_ids: list[int],
    reason: str,
) -> None:
    if not portfolio_ids:
        return
    configs = session.scalars(
        select(PortfolioEvaluatorConfig).where(PortfolioEvaluatorConfig.portfolio_id.in_(portfolio_ids))
    ).all()
    for config in configs:
        config.enabled = False
    now = datetime.now(UTC)
    queued = session.scalars(
        select(EvaluationRun).where(
            EvaluationRun.portfolio_id.in_(portfolio_ids),
            EvaluationRun.status == "queued",
        )
    ).all()
    for run in queued:
        run.status = "cancelled"
        run.finished_at = now
        run.error = reason


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
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise AdminOpError(422, "Agent not found")
        portfolio.agent_id = agent_id
        if not supports_automation(agent.harness):
            _disable_portfolio_automation(
                session,
                [portfolio.id],
                "Cancelled because the portfolio was reassigned to an Agent without integrated automation.",
            )
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
    session.flush()
    reconcile_benchmark_allocations(session)
    session.commit()
    return {"ok": True}


def reset_portfolio(session: Session, portfolio_id: int) -> dict:
    """Delete a contestant's complete allocation history while preserving its
    identity, evaluator configuration, and evaluator audit trail."""
    session.scalars(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update()).one()
    portfolio = writable_portfolio(session, portfolio_id, lock=True)

    cancelled_queued_runs = 0
    cancellation_requested_runs = 0
    now = datetime.now(UTC)
    active_runs = session.scalars(
        select(EvaluationRun)
        .where(
            EvaluationRun.portfolio_id == portfolio.id,
            EvaluationRun.status.in_({"queued", "running", "cancel_requested"}),
        )
        .with_for_update()
    ).all()
    for run in active_runs:
        if run.status == "queued":
            run.status = "cancelled"
            run.finished_at = now
            run.error = "Cancelled because the portfolio was reset."
            cancelled_queued_runs += 1
        else:
            if run.status == "running":
                run.status = "cancel_requested"
            run.error = "Cancellation requested because the portfolio was reset."
            cancellation_requested_runs += 1

    deleted_allocations = int(
        session.scalar(
            select(func.count()).select_from(Allocation).where(Allocation.portfolio_id == portfolio.id)
        )
        or 0
    )
    session.execute(delete(Allocation).where(Allocation.portfolio_id == portfolio.id))
    session.flush()
    reconcile_benchmark_allocations(session)
    session.commit()
    return {
        "ok": True,
        "deleted_allocations": deleted_allocations,
        "cancelled_queued_runs": cancelled_queued_runs,
        "cancellation_requested_runs": cancellation_requested_runs,
    }


def portfolio_admin_detail(session: Session, portfolio_id: int) -> dict:
    """Admin view: public detail plus the handoff fields (per-position notes,
    holding entry/current prices)."""
    if reconcile_benchmark_allocations(session):
        session.commit()
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
    session.flush()
    reconcile_benchmark_allocations(session)
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
    session.flush()
    reconcile_benchmark_allocations(session)
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
