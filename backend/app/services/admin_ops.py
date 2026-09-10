"""Write operations shared by the REST admin router and the MCP tools.

Every experiment-integrity rule lives here exactly once: server-set entry
times, computed effective dates (no backdating), position and signal locking
after the effective boundary, mode separation, and slug uniqueness. Functions own their
`session.commit()` and raise `AdminOpError` on any rule violation; callers
translate that into their transport's error shape (HTTP status / tool error).
"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..models import (
    Agent,
    Allocation,
    ArenaVersion,
    EvaluationRun,
    EvaluatorSettings,
    ModelDefinition,
    ModelHarnessCapability,
    Portfolio,
    PortfolioEvaluatorConfig,
    Position,
    Prompt,
    PromptVersion,
    Setting,
    Signal,
    SignalPosition,
)
from ..seed import (
    LONG_DIRECTION_INSTRUCTIONS_KEY,
    MANAGED_MAX_POSITION_WEIGHT_PCT_KEY,
    MANAGED_MIN_POSITION_WEIGHT_PCT_KEY,
    MANAGED_WRAPPER_PROMPT_KEY,
    REBUILT_MAX_POSITION_WEIGHT_PCT_KEY,
    REBUILT_MIN_POSITION_WEIGHT_PCT_KEY,
    REBUILT_WRAPPER_PROMPT_KEY,
    SHORT_DIRECTION_INSTRUCTIONS_KEY,
)
from ..util import slugify
from .arena import compute_rebuilt_arena, compute_valuations, load_portfolios
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
from .prompt_policy import (
    DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
    DEFAULT_MANAGED_WRAPPER_PROMPT,
    DEFAULT_REBUILT_WRAPPER_PROMPT,
    DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
    PROMPT_DIRECTIONS,
    PROMPT_MODES,
    PROMPT_VERSION_DIRECTIONS,
    allocation_policies_out,
    allocation_policy_from_limits,
    allocation_policy_out,
    prompt_supports_direction,
    prompt_supports_mode,
    validate_direction_instructions,
    validate_position_weights,
    validate_prompt_texts,
    validate_wrapper_prompt,
)
from .serialize import (
    serialize_allocation,
    serialize_detail,
    serialize_rebuilt_detail,
    serialize_signal,
)
from .symbols import (
    SymbolValidationError,
    normalize_symbol,
    resolve_symbol,
    validate_positions,
)
from .trading_calendar import effective_date_for, is_locked

MANAGED_MIN_POSITION_WEIGHT_PCT_FALLBACK = 10.0
MANAGED_MAX_POSITION_WEIGHT_PCT_FALLBACK = 25.0
REBUILT_MIN_POSITION_WEIGHT_PCT_FALLBACK = 10.0
REBUILT_MAX_POSITION_WEIGHT_PCT_FALLBACK = 100.0


def unique_slug(session: Session, model, wanted: str) -> str:
    slug = slugify(wanted)
    candidate = slug
    suffix = 2
    while session.scalars(select(model).where(model.slug == candidate)).first() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def _validate_prompt_mode(prompt_mode: str) -> None:
    if prompt_mode not in PROMPT_MODES:
        raise AdminOpError(422, "Prompt mode must be 'managed' or 'rebuilt'.")


def _validate_direction(direction: str) -> None:
    if direction not in PROMPT_DIRECTIONS:
        raise AdminOpError(422, "Direction must be 'long' or 'short'.")


# --- Models and agents ------------------------------------------------------


def list_models(session: Session) -> dict:
    models = session.scalars(
        select(ModelDefinition)
        .options(selectinload(ModelDefinition.capabilities))
        .order_by(ModelDefinition.slug)
    ).all()
    counts = dict(session.execute(select(Agent.model_id, func.count()).group_by(Agent.model_id)).all())
    run_counts = dict(
        session.execute(select(EvaluationRun.model_id, func.count()).group_by(EvaluationRun.model_id)).all()
    )
    return {
        "models": [
            model_out(
                model, agent_count=counts.get(model.id, 0), evaluation_run_count=run_counts.get(model.id, 0)
            )
            for model in models
        ]
    }


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
        model.name = clean_name
    if notes is not None:
        model.notes = notes
    session.commit()
    count = session.scalar(select(func.count()).select_from(Agent).where(Agent.model_id == model.id))
    run_count = session.scalar(
        select(func.count()).select_from(EvaluationRun).where(EvaluationRun.model_id == model.id)
    )
    return model_out(model, agent_count=count or 0, evaluation_run_count=run_count or 0)


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


def _agent_for_assignment(session: Session, agent_id: int) -> Agent:
    agent = session.scalars(select(Agent).where(Agent.id == agent_id).with_for_update()).first()
    if agent is None:
        raise AdminOpError(422, "Agent not found")
    return agent


def _lock_agent_profile_namespace(session: Session, model_id: int) -> None:
    session.scalar(select(ModelDefinition.id).where(ModelDefinition.id == model_id).with_for_update())


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
    _lock_agent_profile_namespace(session, model.id)
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
    agent = load_agent(session, agent_id, lock=True)
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
    _lock_agent_profile_namespace(session, model.id)
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


def _agent_usage(session: Session) -> tuple[dict[int, int], dict[int, int]]:
    return (
        dict(session.execute(select(Portfolio.agent_id, func.count()).group_by(Portfolio.agent_id)).all()),
        dict(
            session.execute(
                select(EvaluationRun.agent_id, func.count()).group_by(EvaluationRun.agent_id)
            ).all()
        ),
    )


def _admin_agent_out(agent: Agent, portfolio_usage: dict[int, int], run_usage: dict[int, int]) -> dict:
    portfolio_count = portfolio_usage.get(agent.id, 0)
    run_count = run_usage.get(agent.id, 0)
    return {
        **agent_out(agent, portfolio_count=portfolio_count),
        "evaluation_run_count": run_count,
        "can_delete": not (portfolio_count or run_count),
        "delete_blocker": (
            f"{portfolio_count} portfolio(s) and {run_count} evaluation run(s) preserve history."
            if portfolio_count or run_count
            else None
        ),
    }


def list_agents(session: Session) -> dict:
    agents = session.scalars(
        select(Agent)
        .options(selectinload(Agent.model).selectinload(ModelDefinition.capabilities))
        .order_by(Agent.slug)
    ).all()
    portfolio_usage, run_usage = _agent_usage(session)
    return {"agents": [_admin_agent_out(agent, portfolio_usage, run_usage) for agent in agents]}


def delete_agent(session: Session, agent_id: int) -> dict:
    agent = load_agent(session, agent_id, lock=True)
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


def prompt_out(prompt: Prompt, settings: dict) -> dict:
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "name": prompt.name,
        "mode": prompt.mode,
        "direction": prompt.direction,
        "managed_long_text": prompt.managed_long_text,
        "managed_short_text": prompt.managed_short_text,
        "rebuilt_long_text": prompt.rebuilt_long_text,
        "rebuilt_short_text": prompt.rebuilt_short_text,
        "notes": prompt.notes,
        "allocation_policies": allocation_policies_out(settings, prompt),
    }


def _prompt_version_out(version: PromptVersion) -> dict:
    return {
        "version": version.version,
        "name": version.name,
        "mode": version.mode,
        "direction": version.direction,
        "managed_long_text": version.managed_long_text,
        "managed_short_text": version.managed_short_text,
        "rebuilt_long_text": version.rebuilt_long_text,
        "rebuilt_short_text": version.rebuilt_short_text,
        "notes": version.notes,
        "created_at": version.created_at.isoformat(),
        "restored_from_version": (
            version.restored_from.version if version.restored_from is not None else None
        ),
    }


def _admin_prompt_out(
    prompt: Prompt,
    *,
    version_count: int,
    portfolio_count: int,
    settings: dict,
    evaluation_run_count: int = 0,
) -> dict:
    current = prompt.current_version
    if current is None:
        raise RuntimeError(f"Prompt {prompt.id} has no current version")
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "created_at": prompt.created_at.isoformat(),
        "updated_at": prompt.updated_at.isoformat(),
        "current_version": current.version,
        "version_count": version_count,
        "portfolio_count": portfolio_count,
        "evaluation_run_count": evaluation_run_count,
        "can_delete": not (portfolio_count or evaluation_run_count),
        "delete_blocker": "This prompt is used by a portfolio or evaluation run."
        if portfolio_count or evaluation_run_count
        else None,
        "name": current.name,
        "mode": current.mode,
        "direction": current.direction,
        "managed_long_text": current.managed_long_text,
        "managed_short_text": current.managed_short_text,
        "rebuilt_long_text": current.rebuilt_long_text,
        "rebuilt_short_text": current.rebuilt_short_text,
        "notes": current.notes,
        "allocation_policies": allocation_policies_out(settings, prompt),
    }


def _prompt_counts(session: Session, prompt_id: int) -> tuple[int, int]:
    version_count = session.scalar(
        select(func.count()).select_from(PromptVersion).where(PromptVersion.prompt_id == prompt_id)
    )
    portfolio_count = session.scalar(
        select(func.count()).select_from(Portfolio).where(Portfolio.prompt_id == prompt_id)
    )
    return version_count or 0, portfolio_count or 0


def _admin_prompt_with_counts(session: Session, prompt: Prompt) -> dict:
    version_count, portfolio_count = _prompt_counts(session, prompt.id)
    run_count = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .join(PromptVersion, EvaluationRun.prompt_version_id == PromptVersion.id)
            .where(PromptVersion.prompt_id == prompt.id)
        )
        or 0
    )
    return _admin_prompt_out(
        prompt,
        version_count=version_count,
        portfolio_count=portfolio_count,
        evaluation_run_count=run_count,
        settings=get_app_settings(session),
    )


def list_prompts(session: Session) -> dict:
    prompts = session.scalars(select(Prompt).order_by(Prompt.slug)).all()
    return {"prompts": [_admin_prompt_with_counts(session, prompt) for prompt in prompts]}


def delete_prompt(session: Session, prompt_id: int) -> dict:
    prompt = _locked_prompt(session, prompt_id)
    if not _admin_prompt_with_counts(session, prompt)["can_delete"]:
        raise AdminOpError(409, "This prompt is still referenced by a portfolio or evaluation run.")
    prompt.current_version = None
    prompt.current_version_id = None
    session.flush()
    session.delete(prompt)
    session.commit()
    return {"ok": True}


def list_prompt_versions(session: Session, prompt_id: int) -> dict:
    exists = session.scalar(select(Prompt.id).where(Prompt.id == prompt_id))
    if exists is None:
        raise AdminOpError(404, "Prompt not found")
    versions = session.scalars(
        select(PromptVersion)
        .where(PromptVersion.prompt_id == prompt_id)
        .options(joinedload(PromptVersion.restored_from))
        .order_by(PromptVersion.version.desc())
    ).all()
    return {
        "prompt_id": prompt_id,
        "versions": [_prompt_version_out(version) for version in versions],
    }


def _locked_prompt(session: Session, prompt_id: int) -> Prompt:
    prompt = session.scalars(select(Prompt).where(Prompt.id == prompt_id).with_for_update(of=Prompt)).first()
    if prompt is None:
        raise AdminOpError(404, "Prompt not found")
    return prompt


def _prompt_for_portfolio(session: Session, prompt_id: int) -> Prompt:
    prompt = session.scalars(select(Prompt).where(Prompt.id == prompt_id).with_for_update(of=Prompt)).first()
    if prompt is None:
        raise AdminOpError(422, "Prompt not found")
    return prompt


def _validate_prompt_text_contract(
    mode: str,
    direction: str,
    managed_long_text: str | None,
    managed_short_text: str | None,
    rebuilt_long_text: str | None,
    rebuilt_short_text: str | None,
) -> None:
    try:
        validate_prompt_texts(
            mode,
            direction,
            managed_long_text,
            managed_short_text,
            rebuilt_long_text,
            rebuilt_short_text,
        )
    except ValueError as exc:
        raise AdminOpError(422, str(exc)) from None


def _validate_prompt_direction(direction: str) -> None:
    if direction not in PROMPT_VERSION_DIRECTIONS:
        raise AdminOpError(422, "Prompt direction must be 'long', 'short', or 'both'.")


def _ensure_prompt_supports_portfolio_mode(prompt: Prompt, prompt_mode: str) -> None:
    if not prompt_supports_mode(prompt_mode, prompt.mode):
        raise AdminOpError(422, f"Prompt does not support {prompt_mode} portfolios.")


def _ensure_prompt_supports_portfolio_direction(prompt: Prompt, direction: str) -> None:
    if not prompt_supports_direction(direction, prompt.direction):
        raise AdminOpError(422, f"Prompt does not support {direction} portfolios.")


def _ensure_prompt_mode_preserves_references(
    session: Session,
    prompt_id: int,
    version_mode: str,
) -> None:
    removed_modes = [
        prompt_mode
        for prompt_mode in sorted(PROMPT_MODES)
        if not prompt_supports_mode(prompt_mode, version_mode)
    ]
    if not removed_modes:
        return
    references = session.execute(
        select(Portfolio.prompt_mode, func.count())
        .where(
            Portfolio.prompt_id == prompt_id,
            Portfolio.prompt_mode.in_(removed_modes),
        )
        .group_by(Portfolio.prompt_mode)
    ).all()
    if references:
        detail = ", ".join(f"{count} {mode}" for mode, count in references)
        raise AdminOpError(
            409,
            f"Prompt mode cannot remove text used by existing portfolios ({detail}).",
        )


def _ensure_prompt_direction_preserves_references(
    session: Session,
    prompt_id: int,
    version_direction: str,
) -> None:
    removed_directions = [
        direction
        for direction in sorted(PROMPT_DIRECTIONS)
        if not prompt_supports_direction(direction, version_direction)
    ]
    if not removed_directions:
        return
    references = session.execute(
        select(Portfolio.direction, func.count())
        .where(
            Portfolio.prompt_id == prompt_id,
            Portfolio.direction.in_(removed_directions),
        )
        .group_by(Portfolio.direction)
    ).all()
    if references:
        detail = ", ".join(f"{count} {direction}" for direction, count in references)
        raise AdminOpError(
            409,
            f"Prompt direction cannot remove support used by existing portfolios ({detail}).",
        )


def _ensure_prompt_has_no_running_evaluation(session: Session, prompt_id: int) -> None:
    run_count = session.scalar(
        select(func.count())
        .select_from(EvaluationRun)
        .join(Portfolio, Portfolio.id == EvaluationRun.portfolio_id)
        .where(
            Portfolio.prompt_id == prompt_id,
            EvaluationRun.status.in_(("running", "cancel_requested")),
        )
    )
    if run_count:
        raise AdminOpError(
            409,
            "This prompt cannot be changed while a referencing portfolio evaluation is running.",
        )


def _append_prompt_version(
    session: Session,
    prompt: Prompt,
    *,
    name: str,
    mode: str,
    direction: str,
    managed_long_text: str | None,
    managed_short_text: str | None,
    rebuilt_long_text: str | None,
    rebuilt_short_text: str | None,
    notes: str,
    restored_from_version_id: int | None = None,
) -> PromptVersion:
    current = prompt.current_version
    if current is None:
        raise RuntimeError(f"Prompt {prompt.id} has no current version")
    version = PromptVersion(
        prompt_id=prompt.id,
        version=current.version + 1,
        name=name,
        mode=mode,
        direction=direction,
        managed_long_text=managed_long_text,
        managed_short_text=managed_short_text,
        rebuilt_long_text=rebuilt_long_text,
        rebuilt_short_text=rebuilt_short_text,
        notes=notes,
        restored_from_version_id=restored_from_version_id,
    )
    session.add(version)
    session.flush()
    prompt.current_version_id = version.id
    prompt.current_version = version
    return version


def create_prompt(
    session: Session,
    *,
    name: str,
    mode: str,
    direction: str,
    managed_long_text: str | None,
    managed_short_text: str | None,
    rebuilt_long_text: str | None,
    rebuilt_short_text: str | None,
    slug: str | None = None,
    notes: str = "",
) -> dict:
    if not name.strip():
        raise AdminOpError(422, "Prompt name is required")
    _validate_prompt_direction(direction)
    _validate_prompt_text_contract(
        mode,
        direction,
        managed_long_text,
        managed_short_text,
        rebuilt_long_text,
        rebuilt_short_text,
    )
    prompt = Prompt(
        slug=unique_slug(session, Prompt, slug or name),
        current_version_id=None,
    )
    session.add(prompt)
    session.flush()
    version = PromptVersion(
        prompt_id=prompt.id,
        version=1,
        name=name,
        mode=mode,
        direction=direction,
        managed_long_text=managed_long_text,
        managed_short_text=managed_short_text,
        rebuilt_long_text=rebuilt_long_text,
        rebuilt_short_text=rebuilt_short_text,
        notes=notes,
    )
    session.add(version)
    session.flush()
    prompt.current_version_id = version.id
    prompt.current_version = version
    session.commit()
    return _admin_prompt_out(
        prompt,
        version_count=1,
        portfolio_count=0,
        settings=get_app_settings(session),
    )


def update_prompt(
    session: Session,
    prompt_id: int,
    *,
    name: str | None = None,
    mode: str | None = None,
    direction: str | None = None,
    managed_long_text: str | None = None,
    managed_short_text: str | None = None,
    rebuilt_long_text: str | None = None,
    rebuilt_short_text: str | None = None,
    notes: str | None = None,
) -> dict:
    prompt = _locked_prompt(session, prompt_id)
    _ensure_prompt_has_no_running_evaluation(session, prompt_id)
    current = prompt.current_version
    if current is None:
        raise RuntimeError(f"Prompt {prompt.id} has no current version")

    next_name = name if name is not None else current.name
    if not next_name.strip():
        raise AdminOpError(422, "Prompt name is required")
    next_mode = mode if mode is not None else current.mode
    next_direction = direction if direction is not None else current.direction
    _validate_prompt_direction(next_direction)
    supplied_texts = {
        "managed_long_text": managed_long_text,
        "managed_short_text": managed_short_text,
        "rebuilt_long_text": rebuilt_long_text,
        "rebuilt_short_text": rebuilt_short_text,
    }
    next_texts: dict[str, str | None] = {}
    for prompt_mode in sorted(PROMPT_MODES):
        for portfolio_direction in sorted(PROMPT_DIRECTIONS):
            field = f"{prompt_mode}_{portfolio_direction}_text"
            supplied = supplied_texts[field]
            supported = prompt_supports_mode(prompt_mode, next_mode) and prompt_supports_direction(
                portfolio_direction, next_direction
            )
            if not supported:
                if supplied is not None:
                    raise AdminOpError(
                        422,
                        f"{prompt_mode.title()} {portfolio_direction} prompt text must be null for "
                        f"mode '{next_mode}' and direction '{next_direction}'.",
                    )
                next_texts[field] = None
            else:
                next_texts[field] = supplied if supplied is not None else getattr(current, field)
    _validate_prompt_text_contract(
        next_mode,
        next_direction,
        next_texts["managed_long_text"],
        next_texts["managed_short_text"],
        next_texts["rebuilt_long_text"],
        next_texts["rebuilt_short_text"],
    )
    _ensure_prompt_mode_preserves_references(session, prompt.id, next_mode)
    _ensure_prompt_direction_preserves_references(session, prompt.id, next_direction)
    next_notes = notes if notes is not None else current.notes
    changed = (
        next_name != current.name
        or next_mode != current.mode
        or next_direction != current.direction
        or any(getattr(current, field) != value for field, value in next_texts.items())
        or next_notes != current.notes
    )
    if not changed:
        session.commit()
        return _admin_prompt_with_counts(session, prompt)

    _append_prompt_version(
        session,
        prompt,
        name=next_name,
        mode=next_mode,
        direction=next_direction,
        managed_long_text=next_texts["managed_long_text"],
        managed_short_text=next_texts["managed_short_text"],
        rebuilt_long_text=next_texts["rebuilt_long_text"],
        rebuilt_short_text=next_texts["rebuilt_short_text"],
        notes=next_notes,
    )
    session.commit()
    return _admin_prompt_with_counts(session, prompt)


def restore_prompt_version(
    session: Session,
    prompt_id: int,
    version: int,
) -> dict:
    prompt = _locked_prompt(session, prompt_id)
    _ensure_prompt_has_no_running_evaluation(session, prompt_id)
    source = session.scalars(
        select(PromptVersion).where(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.version == version,
        )
    ).first()
    if source is None:
        raise AdminOpError(404, "Prompt version not found")
    _validate_prompt_text_contract(
        source.mode,
        source.direction,
        source.managed_long_text,
        source.managed_short_text,
        source.rebuilt_long_text,
        source.rebuilt_short_text,
    )
    _validate_prompt_direction(source.direction)
    _ensure_prompt_mode_preserves_references(session, prompt.id, source.mode)
    _ensure_prompt_direction_preserves_references(session, prompt.id, source.direction)
    _append_prompt_version(
        session,
        prompt,
        name=source.name,
        mode=source.mode,
        direction=source.direction,
        managed_long_text=source.managed_long_text,
        managed_short_text=source.managed_short_text,
        rebuilt_long_text=source.rebuilt_long_text,
        rebuilt_short_text=source.rebuilt_short_text,
        notes=source.notes,
        restored_from_version_id=source.id,
    )
    session.commit()
    return _admin_prompt_with_counts(session, prompt)


# --- Portfolios -------------------------------------------------------------


def version_out(version: ArenaVersion) -> dict:
    return {
        "id": version.id,
        "name": version.name,
        "evaluation_enabled": version.evaluation_enabled,
        "created_at": version.created_at.isoformat(),
    }


def list_versions(session: Session) -> dict:
    return {
        "versions": [
            version_out(version)
            for version in session.scalars(select(ArenaVersion).order_by(ArenaVersion.id.desc()))
        ]
    }


def _version(session: Session, version_id: int, *, lock: bool = False) -> ArenaVersion:
    query = select(ArenaVersion).where(ArenaVersion.id == version_id)
    if lock:
        query = query.with_for_update()
    version = session.scalar(query)
    if version is None:
        raise AdminOpError(404, "Arena version not found")
    return version


def create_version(session: Session, *, name: str) -> dict:
    if not name.strip():
        raise AdminOpError(422, "Version name is required")
    version = ArenaVersion(name=name.strip(), evaluation_enabled=False)
    session.add(version)
    session.commit()
    return version_out(version)


def update_version(
    session: Session, version_id: int, *, name: str | None = None, evaluation_enabled: bool | None = None
) -> dict:
    # The same lock serializes queue creation, claims, and version pausing.
    session.scalar(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update())
    version = _version(session, version_id, lock=True)
    if name is not None:
        if not name.strip():
            raise AdminOpError(422, "Version name is required")
        version.name = name.strip()
    if evaluation_enabled is not None:
        version.evaluation_enabled = evaluation_enabled
        if not evaluation_enabled:
            now = datetime.now(UTC)
            runs = session.scalars(
                select(EvaluationRun)
                .where(
                    EvaluationRun.portfolio_id.in_(
                        select(Portfolio.id).where(Portfolio.version_id == version_id)
                    ),
                    EvaluationRun.status == "queued",
                )
                .with_for_update()
            ).all()
            for run in runs:
                run.status = "cancelled"
                run.finished_at = now
                run.error = "Cancelled because this Arena version was paused."
    session.commit()
    return version_out(version)


def delete_version(session: Session, version_id: int) -> dict:
    version = _version(session, version_id, lock=True)
    if session.scalar(select(Portfolio.id).where(Portfolio.version_id == version_id).limit(1)) is not None:
        raise AdminOpError(409, "Delete or move every portfolio before deleting this version.")
    session.delete(version)
    session.commit()
    return {"ok": True}


def _portfolio_identity_out(portfolio: Portfolio) -> dict:
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "version_id": portfolio.version_id,
        "version": version_out(portfolio.version),
        "agent_id": portfolio.agent_id,
        "prompt_id": portfolio.prompt_id,
        "prompt_mode": portfolio.prompt_mode,
        "direction": portfolio.direction,
        "execution_boundary": portfolio.execution_boundary,
        "execution_locked": portfolio.execution_locked,
    }


def list_portfolios(session: Session, *, version_id: int | None = None) -> dict:
    query = (
        select(Portfolio)
        .options(
            selectinload(Portfolio.agent)
            .selectinload(Agent.model)
            .selectinload(ModelDefinition.capabilities),
            selectinload(Portfolio.prompt),
            selectinload(Portfolio.version),
        )
        .order_by(func.lower(Portfolio.name), Portfolio.id)
    )
    if version_id is not None:
        _version(session, version_id)
        query = query.where(Portfolio.version_id == version_id)
    portfolios = session.scalars(query).all()
    counts = {
        model: dict(
            session.execute(select(model.portfolio_id, func.count()).group_by(model.portfolio_id)).all()
        )
        for model in (Allocation, Signal, EvaluationRun)
    }
    active_runs = dict(
        session.execute(
            select(EvaluationRun.portfolio_id, func.count())
            .where(EvaluationRun.status.in_({"queued", "running", "cancel_requested"}))
            .group_by(EvaluationRun.portfolio_id)
        ).all()
    )
    rows = []
    for portfolio in portfolios:
        allocation_count = counts[Allocation].get(portfolio.id, 0)
        signal_count = counts[Signal].get(portfolio.id, 0)
        active_count = active_runs.get(portfolio.id, 0)
        structure_blocker = (
            "Wait for active evaluations to stop."
            if active_count
            else "Reset portfolio history before changing track or direction."
            if allocation_count or signal_count
            else None
        )
        rows.append(
            {
                **_portfolio_identity_out(portfolio),
                "agent": agent_out(portfolio.agent),
                "prompt": {
                    "id": portfolio.prompt.id,
                    "slug": portfolio.prompt.slug,
                    "name": portfolio.prompt.name,
                },
                "allocation_count": allocation_count,
                "signal_count": signal_count,
                "evaluation_run_count": counts[EvaluationRun].get(portfolio.id, 0),
                "active_run_count": active_count,
                "structure_editable": structure_blocker is None,
                "structure_blocker": structure_blocker,
                "prompt_editable": not active_count,
                "timing_editable": not portfolio.execution_locked and not active_count,
            }
        )
    return {"portfolios": rows}


def writable_portfolio(session: Session, portfolio_id: int, *, lock: bool = False) -> Portfolio:
    query = select(Portfolio).where(Portfolio.id == portfolio_id)
    if lock:
        query = query.with_for_update()
    portfolio = session.scalars(query).first()
    if portfolio is None:
        raise AdminOpError(404, "Portfolio not found")
    return portfolio


def _lock_portfolio_lifecycle(session: Session, portfolio_id: int) -> Portfolio:
    # Queue operations lock settings first; submissions lock their run before
    # inserting decisions. Follow that order before taking the portfolio lock.
    session.scalars(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update()).one()
    session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.portfolio_id == portfolio_id)
        .order_by(EvaluationRun.id)
        .with_for_update()
    ).all()
    return writable_portfolio(session, portfolio_id, lock=True)


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
    prompt_mode: str,
    direction: str,
    version_id: int,
    execution_boundary: str = "close",
    slug: str | None = None,
) -> dict:
    session.scalar(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update())
    _validate_prompt_mode(prompt_mode)
    _validate_direction(direction)
    if execution_boundary not in {"open", "close"}:
        raise AdminOpError(422, "Execution boundary must be open or close")
    version = _version(session, version_id, lock=True)
    agent = _agent_for_assignment(session, agent_id)
    prompt = _prompt_for_portfolio(session, prompt_id)
    _ensure_prompt_supports_portfolio_mode(prompt, prompt_mode)
    _ensure_prompt_supports_portfolio_direction(prompt, direction)
    clean_name = name.strip()
    if not clean_name:
        raise AdminOpError(422, "Portfolio name is required")
    requested_slug = slugify(slug or clean_name)
    if clean_name.casefold() == "spy" or requested_slug == "spy":
        raise AdminOpError(409, "SPY is reserved for the synthetic benchmark reference.")
    portfolio = Portfolio(
        slug=unique_slug(session, Portfolio, requested_slug),
        name=clean_name,
        agent_id=agent.id,
        prompt_id=prompt.id,
        prompt_mode=prompt_mode,
        direction=direction,
        version=version,
        execution_boundary=execution_boundary,
        execution_locked=False,
    )
    session.add(portfolio)
    session.commit()
    return _portfolio_identity_out(portfolio)


def update_portfolio(
    session: Session,
    portfolio_id: int,
    *,
    name: str | None = None,
    agent_id: int | None = None,
    prompt_id: int | None = None,
    prompt_mode: str | None = None,
    direction: str | None = None,
    version_id: int | None = None,
    execution_boundary: str | None = None,
) -> dict:
    portfolio = _lock_portfolio_lifecycle(session, portfolio_id)
    mode = prompt_mode if prompt_mode is not None else portfolio.prompt_mode
    direction = direction if direction is not None else portfolio.direction
    _validate_prompt_mode(mode)
    _validate_direction(direction)
    active_count = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(
                EvaluationRun.portfolio_id == portfolio_id,
                EvaluationRun.status.in_({"queued", "running", "cancel_requested"}),
            )
        )
        or 0
    )
    structural_change = mode != portfolio.prompt_mode or direction != portfolio.direction
    assignment_change = (
        (agent_id is not None and agent_id != portfolio.agent_id)
        or (prompt_id is not None and prompt_id != portfolio.prompt_id)
        or (version_id is not None and version_id != portfolio.version_id)
        or (execution_boundary is not None and execution_boundary != portfolio.execution_boundary)
    )
    if active_count and (structural_change or assignment_change):
        raise AdminOpError(
            409, "Wait for active evaluations to stop before changing portfolio configuration."
        )
    if structural_change and any(
        session.scalar(select(model.id).where(model.portfolio_id == portfolio_id).limit(1))
        for model in (Allocation, Signal)
    ):
        raise AdminOpError(409, "Reset the portfolio's history before changing its prompt mode or direction.")
    if execution_boundary is not None and execution_boundary != portfolio.execution_boundary:
        if execution_boundary not in {"open", "close"}:
            raise AdminOpError(422, "Execution boundary must be open or close")
        if portfolio.execution_locked:
            raise AdminOpError(
                409, "Create a new portfolio to change execution timing after its first decision."
            )
        portfolio.execution_boundary = execution_boundary
    prompt = _prompt_for_portfolio(session, prompt_id) if prompt_id is not None else portfolio.prompt
    _ensure_prompt_supports_portfolio_mode(prompt, mode)
    _ensure_prompt_supports_portfolio_direction(prompt, direction)
    if name is not None:
        if not name.strip():
            raise AdminOpError(422, "Portfolio name is required")
        if name.strip().casefold() == "spy":
            raise AdminOpError(409, "SPY is reserved for the synthetic benchmark reference.")
        portfolio.name = name.strip()
    if agent_id is not None:
        agent = _agent_for_assignment(session, agent_id)
        portfolio.agent_id = agent.id
        if not supports_automation(agent.harness):
            _disable_portfolio_automation(
                session, [portfolio.id], "Agent does not support integrated automation."
            )
    if version_id is not None:
        portfolio.version = _version(session, version_id, lock=True)
    portfolio.prompt = prompt
    if portfolio.prompt_mode != mode and mode == "rebuilt" and portfolio.evaluator_config is not None:
        portfolio.evaluator_config.weekdays = [0, 1, 2, 3, 4]
    portfolio.prompt_mode = mode
    portfolio.direction = direction
    session.commit()
    return _portfolio_identity_out(portfolio)


def delete_portfolio(session: Session, portfolio_id: int) -> dict:
    portfolio = _lock_portfolio_lifecycle(session, portfolio_id)
    session.delete(portfolio)
    session.commit()
    return {"ok": True}


def reset_portfolio(session: Session, portfolio_id: int) -> dict:
    """Delete all decisions and evaluation runs, preserving identity and configuration."""
    portfolio = _lock_portfolio_lifecycle(session, portfolio_id)
    deleted = {}
    for model, key in (
        (EvaluationRun, "deleted_evaluation_runs"),
        (Allocation, "deleted_allocations"),
        (Signal, "deleted_signals"),
    ):
        deleted[key] = session.execute(delete(model).where(model.portfolio_id == portfolio.id)).rowcount
    session.commit()
    return {"ok": True, **deleted}


def portfolio_admin_detail(session: Session, portfolio_id: int) -> dict:
    """Admin view: public detail plus the handoff fields (per-position notes,
    holding entry/current prices)."""
    portfolios = load_portfolios(session)
    match = next((p for p in portfolios if p.id == portfolio_id), None)
    if match is None:
        raise AdminOpError(404, "Portfolio not found")
    settings = get_app_settings(session)
    wrapper_prompt = settings[f"{match.prompt_mode}_wrapper_prompt"]
    direction_instructions = settings[f"{match.direction}_direction_instructions"]
    allocation_policy = allocation_policy_out(settings, match.prompt_mode)
    if match.prompt_mode == "rebuilt":
        same_direction = [
            portfolio
            for portfolio in portfolios
            if portfolio.prompt_mode == "rebuilt"
            and portfolio.direction == match.direction
            and portfolio.version_id == match.version_id
        ]
        arena = compute_rebuilt_arena(
            session,
            same_direction,
        )
        analysis = arena.by_portfolio_id.get(match.id)
        if analysis is None:
            raise AdminOpError(404, "Portfolio not found")
        return {
            "as_of": arena.as_of,
            "market_data_status": arena.market_data_status,
            "portfolio": serialize_rebuilt_detail(
                analysis,
                arena,
                allocation_policy,
                direction_instructions,
                admin=True,
                wrapper_prompt=wrapper_prompt,
            ),
        }

    valuations = compute_valuations(session, [match])
    valuation = valuations.by_portfolio_id.get(match.id)
    if valuation is None:
        raise AdminOpError(404, "Portfolio not found")
    return {
        "as_of": valuations.as_of,
        "market_data_status": valuations.market_data_status,
        "portfolio": serialize_detail(
            valuation,
            valuations,
            allocation_policy,
            direction_instructions,
            admin=True,
            wrapper_prompt=wrapper_prompt,
        ),
    }


# --- Allocations ------------------------------------------------------------


def _normalize_positions(policy: dict, positions: list[dict]) -> list[dict]:
    """Normalize symbols and enforce the position-set rules (sum to 100, no dups,
    positive whole-book weights) plus per-symbol resolution against Massive."""
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
        validate_position_weights(policy, normalized)
    except SymbolValidationError as exc:
        raise AdminOpError(422, exc.message) from None
    except ValueError as exc:
        raise AdminOpError(422, str(exc)) from None
    return normalized


def _ensure_managed_not_liquidated(session: Session, portfolio: Portfolio) -> None:
    if portfolio.prompt_mode != "managed" or portfolio.direction != "short" or not portfolio.allocations:
        return
    valuations = compute_valuations(session, [portfolio])
    valuation = valuations.by_portfolio_id.get(portfolio.id)
    if valuation is not None and valuation.result is not None and valuation.result.liquidated_at:
        raise AdminOpError(
            409,
            "This short portfolio is liquidated. Reset its history before entering another allocation.",
        )


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
    portfolio.execution_locked = True
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
    first matching market boundary strictly after it (no backdating). Rejects a clash if an
    allocation already takes effect that date — edit that one instead."""
    portfolio = writable_portfolio(session, portfolio_id, lock=True)
    if portfolio.prompt_mode != "managed":
        raise AdminOpError(409, "Rebuilt portfolios accept daily signals, not allocations.")
    _ensure_managed_not_liquidated(session, portfolio)

    policy = allocation_policy_out(get_app_settings(session), "managed")
    normalized = _normalize_positions(policy, positions)
    now = datetime.now(UTC)
    effective = effective_date_for(now, portfolio.execution_boundary)
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
    """Positions are frozen once the effective boundary has passed; the note stays
    editable forever."""
    allocation = session.scalars(
        select(Allocation)
        .where(Allocation.id == allocation_id)
        .options(selectinload(Allocation.positions), selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise AdminOpError(404, "Allocation not found")

    if positions is not None:
        if is_locked(allocation.effective_date, datetime.now(UTC), allocation.portfolio.execution_boundary):
            raise AdminOpError(
                403,
                "Positions are frozen: the effective boundary has passed. Enter a new rebalance instead.",
            )
        _ensure_managed_not_liquidated(session, allocation.portfolio)
        policy = allocation_policy_out(get_app_settings(session), "managed")
        normalized = _normalize_positions(policy, positions)
        allocation.positions.clear()
        session.flush()  # delete old rows before inserting (unique on allocation+symbol)
        _apply_positions(allocation, normalized)

    if note is not None:
        allocation.note = note

    session.commit()
    return serialize_allocation(reload_allocation(session, allocation.id), admin=True)


def _delete_decision(session: Session, model: type[Allocation] | type[Signal], decision_id: int) -> dict:
    portfolio_id = session.scalar(select(model.portfolio_id).where(model.id == decision_id))
    if portfolio_id is None:
        raise AdminOpError(404, f"{model.__name__} not found")
    _lock_portfolio_lifecycle(session, portfolio_id)
    decision = session.get(model, decision_id, populate_existing=True)
    if decision is None:
        raise AdminOpError(404, f"{model.__name__} not found")
    result_column = EvaluationRun.allocation_id if model is Allocation else EvaluationRun.signal_id
    session.execute(delete(EvaluationRun).where(result_column == decision_id))
    session.delete(decision)
    session.commit()
    return {"ok": True}


def delete_allocation(session: Session, allocation_id: int) -> dict:
    return _delete_decision(session, Allocation, allocation_id)


# --- Rebuilt signals --------------------------------------------------------


SIGNAL_PROVENANCE = {"integrated", "browser_admin", "mcp"}


def _apply_signal_positions(signal: Signal, positions: list[dict]) -> None:
    for position in positions:
        signal.positions.append(
            SignalPosition(
                symbol=position["symbol"],
                weight_pct=position["weight_pct"],
                note=position["note"],
            )
        )


def _new_signal(
    portfolio: Portfolio,
    positions: list[dict],
    note: str,
    entered_at: datetime,
    effective_date: date,
    provenance: str,
) -> Signal:
    if provenance not in SIGNAL_PROVENANCE:
        raise AdminOpError(422, "Invalid signal provenance.")
    portfolio.execution_locked = True
    signal = Signal(
        portfolio_id=portfolio.id,
        entered_at=entered_at,
        effective_date=effective_date,
        note=note,
        provenance=provenance,
    )
    _apply_signal_positions(signal, positions)
    return signal


def reload_signal(session: Session, signal_id: int) -> Signal:
    return session.scalars(
        select(Signal).where(Signal.id == signal_id).options(selectinload(Signal.positions))
    ).one()


def create_signal(
    session: Session,
    portfolio_id: int,
    positions: list[dict],
    note: str = "",
    *,
    provenance: str = "mcp",
    now: datetime | None = None,
) -> dict:
    """Create one independent rebuilt signal for the next future matching boundary."""
    portfolio = writable_portfolio(session, portfolio_id, lock=True)
    if portfolio.prompt_mode != "rebuilt":
        raise AdminOpError(409, "Managed portfolios accept allocations, not daily signals.")
    if provenance not in {"browser_admin", "mcp"}:
        raise AdminOpError(422, "Only browser_admin or mcp provenance is accepted here.")

    current_time = now or datetime.now(UTC)
    effective = effective_date_for(current_time, portfolio.execution_boundary)
    policy = allocation_policy_out(get_app_settings(session), "rebuilt")
    normalized = _normalize_positions(policy, positions)
    clash = session.scalars(
        select(Signal).where(
            Signal.portfolio_id == portfolio.id,
            Signal.effective_date == effective,
        )
    ).first()
    if clash is not None:
        raise AdminOpError(
            409,
            f"A signal already targets {effective.isoformat()} — edit it instead.",
        )

    signal = _new_signal(
        portfolio,
        normalized,
        note,
        current_time,
        effective,
        provenance,
    )
    session.add(signal)
    session.commit()
    return serialize_signal(reload_signal(session, signal.id), admin=True, now=current_time)


def update_signal(
    session: Session,
    signal_id: int,
    positions: list[dict] | None = None,
    note: str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    signal = session.scalars(
        select(Signal)
        .where(Signal.id == signal_id)
        .options(selectinload(Signal.positions), selectinload(Signal.portfolio))
        .with_for_update()
    ).first()
    if signal is None:
        raise AdminOpError(404, "Signal not found.")
    if is_locked(signal.effective_date, current_time, signal.portfolio.execution_boundary):
        raise AdminOpError(403, "This signal is immutable: its effective boundary has passed.")
    if positions is not None:
        policy = allocation_policy_out(get_app_settings(session), "rebuilt")
        normalized = _normalize_positions(policy, positions)
        signal.positions.clear()
        session.flush()
        _apply_signal_positions(signal, normalized)
    if note is not None:
        signal.note = note
    session.commit()
    return serialize_signal(reload_signal(session, signal.id), admin=True, now=current_time)


def delete_signal(session: Session, signal_id: int) -> dict:
    return _delete_decision(session, Signal, signal_id)


# --- Settings ---------------------------------------------------------------


def _setting_value(session: Session, key: str, fallback: str) -> str:
    setting = session.get(Setting, key)
    return setting.value if setting is not None else fallback


def _setting_float(session: Session, key: str, fallback: float) -> float:
    return float(_setting_value(session, key, str(fallback)))


def get_app_settings(session: Session) -> dict:
    return {
        "managed_allocation_policy": allocation_policy_from_limits(
            _setting_float(
                session,
                MANAGED_MIN_POSITION_WEIGHT_PCT_KEY,
                MANAGED_MIN_POSITION_WEIGHT_PCT_FALLBACK,
            ),
            _setting_float(
                session,
                MANAGED_MAX_POSITION_WEIGHT_PCT_KEY,
                MANAGED_MAX_POSITION_WEIGHT_PCT_FALLBACK,
            ),
        ),
        "rebuilt_allocation_policy": allocation_policy_from_limits(
            _setting_float(
                session,
                REBUILT_MIN_POSITION_WEIGHT_PCT_KEY,
                REBUILT_MIN_POSITION_WEIGHT_PCT_FALLBACK,
            ),
            _setting_float(
                session,
                REBUILT_MAX_POSITION_WEIGHT_PCT_KEY,
                REBUILT_MAX_POSITION_WEIGHT_PCT_FALLBACK,
            ),
        ),
        "managed_wrapper_prompt": _setting_value(
            session,
            MANAGED_WRAPPER_PROMPT_KEY,
            DEFAULT_MANAGED_WRAPPER_PROMPT,
        ),
        "rebuilt_wrapper_prompt": _setting_value(
            session,
            REBUILT_WRAPPER_PROMPT_KEY,
            DEFAULT_REBUILT_WRAPPER_PROMPT,
        ),
        "long_direction_instructions": _setting_value(
            session,
            LONG_DIRECTION_INSTRUCTIONS_KEY,
            DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
        ),
        "short_direction_instructions": _setting_value(
            session,
            SHORT_DIRECTION_INSTRUCTIONS_KEY,
            DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
        ),
    }


def wrapper_prompt_for_portfolio(session: Session, portfolio: Portfolio) -> str:
    settings = get_app_settings(session)
    if portfolio.prompt_mode == "managed":
        return settings["managed_wrapper_prompt"]
    if portfolio.prompt_mode == "rebuilt":
        return settings["rebuilt_wrapper_prompt"]
    raise ValueError("Contestant portfolio is missing a valid prompt mode")


def update_app_settings(
    session: Session,
    *,
    managed_allocation_policy: dict,
    rebuilt_allocation_policy: dict,
    managed_wrapper_prompt: str,
    rebuilt_wrapper_prompt: str,
    long_direction_instructions: str,
    short_direction_instructions: str,
) -> dict:
    try:
        managed_policy = allocation_policy_from_limits(
            float(managed_allocation_policy["min_position_weight_pct"]),
            float(managed_allocation_policy["max_position_weight_pct"]),
        )
        rebuilt_policy = allocation_policy_from_limits(
            float(rebuilt_allocation_policy["min_position_weight_pct"]),
            float(rebuilt_allocation_policy["max_position_weight_pct"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AdminOpError(422, str(exc)) from None
    values = {
        MANAGED_MIN_POSITION_WEIGHT_PCT_KEY: str(managed_policy["min_position_weight_pct"]),
        MANAGED_MAX_POSITION_WEIGHT_PCT_KEY: str(managed_policy["max_position_weight_pct"]),
        REBUILT_MIN_POSITION_WEIGHT_PCT_KEY: str(rebuilt_policy["min_position_weight_pct"]),
        REBUILT_MAX_POSITION_WEIGHT_PCT_KEY: str(rebuilt_policy["max_position_weight_pct"]),
        MANAGED_WRAPPER_PROMPT_KEY: validate_wrapper_prompt(managed_wrapper_prompt),
        REBUILT_WRAPPER_PROMPT_KEY: validate_wrapper_prompt(rebuilt_wrapper_prompt),
        LONG_DIRECTION_INSTRUCTIONS_KEY: validate_direction_instructions(long_direction_instructions),
        SHORT_DIRECTION_INSTRUCTIONS_KEY: validate_direction_instructions(short_direction_instructions),
    }
    for key, value in values.items():
        setting = session.get(Setting, key)
        if setting is None:
            session.add(Setting(key=key, value=value))
        else:
            setting.value = value
    session.commit()
    return get_app_settings(session)
