"""Write operations shared by the REST admin router and the MCP tools.

Every experiment-integrity rule lives here exactly once: server-set entry
times, computed effective dates (no backdating), position and signal locking
after the effective close, mode separation, and slug uniqueness. Functions own their
`session.commit()` and raise `AdminOpError` on any rule violation; callers
translate that into their transport's error shape (HTTP status / tool error).
"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..models import (
    Agent,
    Allocation,
    EvaluationRun,
    EvaluatorSettings,
    MetaPortfolioSet,
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
    DEFAULT_COST_BPS_KEY,
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

DEFAULT_COST_BPS_FALLBACK = 10
MANAGED_MIN_POSITION_WEIGHT_PCT_FALLBACK = 10.0
MANAGED_MAX_POSITION_WEIGHT_PCT_FALLBACK = 25.0
REBUILT_MIN_POSITION_WEIGHT_PCT_FALLBACK = 10.0
REBUILT_MAX_POSITION_WEIGHT_PCT_FALLBACK = 100.0
PROMPT_CONTEXT_SCOPES = {"portfolio", "arena"}
META_PORTFOLIO_COST_BPS = 10
META_PORTFOLIO_CELLS = (
    ("Core", "managed", "long"),
    ("Pulse", "rebuilt", "long"),
    ("Shadow", "managed", "short"),
    ("Probe", "rebuilt", "short"),
)


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


def _validate_prompt_context_scope(context_scope: str) -> None:
    if context_scope not in PROMPT_CONTEXT_SCOPES:
        raise AdminOpError(422, "Prompt context scope must be 'portfolio' or 'arena'.")


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


def prompt_out(prompt: Prompt, settings: dict) -> dict:
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "context_scope": prompt.context_scope,
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
) -> dict:
    current = prompt.current_version
    if current is None:
        raise RuntimeError(f"Prompt {prompt.id} has no current version")
    return {
        "id": prompt.id,
        "slug": prompt.slug,
        "context_scope": prompt.context_scope,
        "status": prompt.status,
        "archived_at": prompt.archived_at.isoformat() if prompt.archived_at is not None else None,
        "created_at": prompt.created_at.isoformat(),
        "updated_at": prompt.updated_at.isoformat(),
        "current_version": current.version,
        "version_count": version_count,
        "portfolio_count": portfolio_count,
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
    return _admin_prompt_out(
        prompt,
        version_count=version_count,
        portfolio_count=portfolio_count,
        settings=get_app_settings(session),
    )


def list_prompts(session: Session, *, status: str | None = None) -> dict:
    if status is not None and status not in {"all", "active", "archived"}:
        raise AdminOpError(422, "Prompt status must be 'all', 'active', or 'archived'.")
    query = select(Prompt).order_by(Prompt.slug)
    if status in {"active", "archived"}:
        query = query.where(Prompt.status == status)
    prompts = session.scalars(query).all()
    prompt_ids = [prompt.id for prompt in prompts]
    if not prompt_ids:
        return {"prompts": []}
    version_counts = dict(
        session.execute(
            select(PromptVersion.prompt_id, func.count())
            .where(PromptVersion.prompt_id.in_(prompt_ids))
            .group_by(PromptVersion.prompt_id)
        ).all()
    )
    portfolio_counts = dict(
        session.execute(
            select(Portfolio.prompt_id, func.count())
            .where(Portfolio.prompt_id.in_(prompt_ids))
            .group_by(Portfolio.prompt_id)
        ).all()
    )
    settings = get_app_settings(session)
    return {
        "prompts": [
            _admin_prompt_out(
                prompt,
                version_count=version_counts.get(prompt.id, 0),
                portfolio_count=portfolio_counts.get(prompt.id, 0),
                settings=settings,
            )
            for prompt in prompts
        ]
    }


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


def _active_prompt_for_portfolio(session: Session, prompt_id: int) -> Prompt:
    prompt = session.scalars(select(Prompt).where(Prompt.id == prompt_id).with_for_update(of=Prompt)).first()
    if prompt is None or prompt.status != "active":
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
            f"Prompt mode cannot remove text used by existing active or archived portfolios ({detail}).",
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
            "Prompt direction cannot remove support used by existing active or archived "
            f"portfolios ({detail}).",
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
    context_scope: str = "portfolio",
    slug: str | None = None,
    notes: str = "",
) -> dict:
    if not name.strip():
        raise AdminOpError(422, "Prompt name is required")
    _validate_prompt_context_scope(context_scope)
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
        context_scope=context_scope,
        status="active",
        archived_at=None,
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
    if prompt.status != "active":
        raise AdminOpError(409, "Archived prompts cannot be updated.")
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
        version_count, portfolio_count = _prompt_counts(session, prompt.id)
        session.commit()
        return _admin_prompt_out(
            prompt,
            version_count=version_count,
            portfolio_count=portfolio_count,
            settings=get_app_settings(session),
        )

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


def archive_prompt(session: Session, prompt_id: int) -> dict:
    prompt = _locked_prompt(session, prompt_id)
    active_count = session.scalar(
        select(func.count())
        .select_from(Portfolio)
        .where(
            Portfolio.prompt_id == prompt_id,
            Portfolio.status == "active",
        )
    )
    if active_count:
        raise AdminOpError(409, "Archive every portfolio using this prompt before archiving the prompt.")
    if prompt.status == "active":
        prompt.status = "archived"
        prompt.archived_at = datetime.now(UTC)
    session.commit()
    return _admin_prompt_with_counts(session, prompt)


def unarchive_prompt(session: Session, prompt_id: int) -> dict:
    prompt = _locked_prompt(session, prompt_id)
    if prompt.status == "archived":
        prompt.status = "active"
        prompt.archived_at = None
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


def writable_portfolio(session: Session, portfolio_id: int, *, lock: bool = False) -> Portfolio:
    query = select(Portfolio).where(Portfolio.id == portfolio_id)
    if lock:
        query = query.with_for_update()
    portfolio = session.scalars(query).first()
    if portfolio is None:
        raise AdminOpError(404, "Portfolio not found")
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
    prompt_mode: str,
    direction: str,
    slug: str | None = None,
    cost_bps: int | None = None,
) -> dict:
    _validate_prompt_mode(prompt_mode)
    _validate_direction(direction)
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(422, "Agent not found")
    prompt = _active_prompt_for_portfolio(session, prompt_id)
    if prompt.context_scope != "portfolio":
        raise AdminOpError(
            422,
            "Arena-scoped prompts can only be used through meta portfolio set creation.",
        )
    _ensure_prompt_supports_portfolio_mode(prompt, prompt_mode)
    _ensure_prompt_supports_portfolio_direction(prompt, direction)
    clean_name = name.strip()
    requested_slug = slugify(slug or clean_name)
    if clean_name.casefold() == "spy" or requested_slug == "spy":
        raise AdminOpError(409, "SPY is reserved for the synthetic benchmark reference.")

    portfolio = Portfolio(
        slug=unique_slug(session, Portfolio, requested_slug),
        name=name,
        agent_id=agent.id,
        prompt_id=prompt.id,
        prompt_mode=prompt_mode,
        direction=direction,
        cost_bps=_default_cost_bps(session) if cost_bps is None else cost_bps,
    )
    session.add(portfolio)
    session.commit()
    return {
        "id": portfolio.id,
        "slug": portfolio.slug,
        "name": portfolio.name,
        "prompt_mode": portfolio.prompt_mode,
        "direction": portfolio.direction,
        "cost_bps": portfolio.cost_bps,
    }


def _meta_portfolio_set_out(meta_set: MetaPortfolioSet) -> dict:
    return {
        "id": meta_set.id,
        "slug": meta_set.slug,
        "family_name": meta_set.family_name,
        "agent_id": meta_set.agent_id,
        "prompt_id": meta_set.prompt_id,
        "created_at": meta_set.created_at.isoformat(),
        "portfolios": [
            {
                "id": portfolio.id,
                "slug": portfolio.slug,
                "name": portfolio.name,
                "prompt_mode": portfolio.prompt_mode,
                "direction": portfolio.direction,
                "cost_bps": portfolio.cost_bps,
                "evaluator": {
                    "enabled": portfolio.evaluator_config.enabled,
                    "weekdays": portfolio.evaluator_config.weekdays,
                },
            }
            for portfolio in meta_set.portfolios
        ],
    }


def create_meta_portfolio_set(
    session: Session,
    *,
    family_name: str,
    agent_id: int,
    prompt_id: int,
) -> dict:
    """Atomically create and automate all four synthesis cells for one family."""
    clean_family_name = family_name.strip()
    if not clean_family_name:
        raise AdminOpError(422, "Meta portfolio family name is required.")
    if len(clean_family_name) > 180:
        raise AdminOpError(422, "Meta portfolio family name must be at most 180 characters.")

    # Serialize identity checks and creation so two concurrent requests cannot
    # each pass the friendly conflict checks before inserting.
    session.scalars(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update()).one()

    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AdminOpError(422, "Agent not found")
    if not supports_automation(agent.harness):
        raise AdminOpError(422, "The selected agent does not support integrated automation.")

    prompt = _active_prompt_for_portfolio(session, prompt_id)
    if prompt.context_scope != "arena":
        raise AdminOpError(422, "Meta portfolio sets require an arena-scoped prompt.")
    if prompt.mode != "both" or prompt.direction != "both":
        raise AdminOpError(
            422,
            "Meta portfolio sets require a prompt supporting managed and rebuilt, long and short.",
        )

    family_slug = slugify(clean_family_name)
    if session.scalar(select(MetaPortfolioSet.id).where(MetaPortfolioSet.slug == family_slug)):
        raise AdminOpError(409, "A meta portfolio set with this family name already exists.")

    members = [
        {
            "name": f"{clean_family_name} {suffix}",
            "slug": slugify(f"{clean_family_name} {suffix}"),
            "prompt_mode": prompt_mode,
            "direction": direction,
        }
        for suffix, prompt_mode, direction in META_PORTFOLIO_CELLS
    ]
    member_slugs = [member["slug"] for member in members]
    member_names = [member["name"].casefold() for member in members]
    conflicting_portfolio = session.scalar(
        select(Portfolio.id).where(
            (Portfolio.slug.in_(member_slugs)) | (func.lower(Portfolio.name).in_(member_names))
        )
    )
    if conflicting_portfolio is not None:
        raise AdminOpError(409, "One or more meta portfolio member identities already exist.")

    meta_set = MetaPortfolioSet(
        slug=family_slug,
        family_name=clean_family_name,
        agent_id=agent.id,
        prompt_id=prompt.id,
    )
    session.add(meta_set)
    session.flush()
    for member in members:
        portfolio = Portfolio(
            slug=member["slug"],
            name=member["name"],
            agent_id=agent.id,
            prompt_id=prompt.id,
            meta_set_id=meta_set.id,
            prompt_mode=member["prompt_mode"],
            direction=member["direction"],
            cost_bps=META_PORTFOLIO_COST_BPS,
        )
        portfolio.evaluator_config = PortfolioEvaluatorConfig(
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        session.add(portfolio)

    session.commit()
    return _meta_portfolio_set_out(meta_set)


def update_portfolio(
    session: Session,
    portfolio_id: int,
    *,
    name: str | None = None,
    status: str | None = None,
    agent_id: int | None = None,
    prompt_id: int | None = None,
    prompt_mode: str | None = None,
    direction: str | None = None,
    cost_bps: int | None = None,
) -> dict:
    changing_structure = prompt_mode is not None or direction is not None or prompt_id is not None
    if changing_structure:
        session.scalars(select(EvaluatorSettings).where(EvaluatorSettings.id == 1).with_for_update()).one()
    portfolio = writable_portfolio(session, portfolio_id, lock=changing_structure)
    if prompt_mode is not None:
        _validate_prompt_mode(prompt_mode)
    if direction is not None:
        _validate_direction(direction)
    final_prompt_mode = prompt_mode if prompt_mode is not None else portfolio.prompt_mode
    final_direction = direction if direction is not None else portfolio.direction
    if prompt_id is not None or prompt_mode is not None or direction is not None:
        final_prompt_id = prompt_id if prompt_id is not None else portfolio.prompt_id
        final_prompt = _active_prompt_for_portfolio(session, final_prompt_id)
    else:
        final_prompt = portfolio.prompt
    if portfolio.meta_set_id is None:
        if final_prompt.context_scope != "portfolio":
            raise AdminOpError(
                422,
                "Arena-scoped prompts can only be used through meta portfolio set creation.",
            )
    else:
        meta_set = session.get(MetaPortfolioSet, portfolio.meta_set_id)
        if meta_set is None:
            raise RuntimeError(f"Portfolio {portfolio.id} references a missing meta portfolio set")
        if (
            final_prompt.id != meta_set.prompt_id
            or final_prompt_mode != portfolio.prompt_mode
            or final_direction != portfolio.direction
            or (agent_id is not None and agent_id != meta_set.agent_id)
        ):
            raise AdminOpError(
                409,
                "A meta portfolio's prompt, mode, direction, and agent are managed by its set.",
            )
    _ensure_prompt_supports_portfolio_mode(final_prompt, final_prompt_mode)
    _ensure_prompt_supports_portfolio_direction(final_prompt, final_direction)
    changing_prompt = prompt_id is not None and prompt_id != portfolio.prompt_id
    if name is not None:
        if name.strip().casefold() == "spy":
            raise AdminOpError(409, "SPY is reserved for the synthetic benchmark reference.")
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
        portfolio.prompt_id = prompt_id
    changing_mode = prompt_mode is not None and prompt_mode != portfolio.prompt_mode
    changing_direction = direction is not None and direction != portfolio.direction
    if changing_prompt:
        running_count = session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(
                EvaluationRun.portfolio_id == portfolio.id,
                EvaluationRun.status.in_({"running", "cancel_requested"}),
            )
        )
        if running_count:
            raise AdminOpError(
                409,
                "Wait for the active evaluation to finish before changing its prompt.",
            )
    if changing_mode or changing_direction:
        allocation_count = int(
            session.scalar(
                select(func.count()).select_from(Allocation).where(Allocation.portfolio_id == portfolio.id)
            )
            or 0
        )
        signal_count = int(
            session.scalar(
                select(func.count()).select_from(Signal).where(Signal.portfolio_id == portfolio.id)
            )
            or 0
        )
        active_run_count = int(
            session.scalar(
                select(func.count())
                .select_from(EvaluationRun)
                .where(
                    EvaluationRun.portfolio_id == portfolio.id,
                    EvaluationRun.status.in_({"queued", "running", "cancel_requested"}),
                )
            )
            or 0
        )
        if allocation_count or signal_count or portfolio.founding_v2 or active_run_count:
            raise AdminOpError(
                409,
                "Reset the portfolio's history before changing its prompt mode or direction.",
            )
    if changing_mode:
        portfolio.prompt_mode = prompt_mode
        portfolio.founding_v2 = False
        if prompt_mode == "rebuilt" and portfolio.evaluator_config is not None:
            portfolio.evaluator_config.weekdays = [0, 1, 2, 3, 4]
    if changing_direction:
        portfolio.direction = direction
        portfolio.founding_v2 = False
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
        "prompt_mode": portfolio.prompt_mode,
        "direction": portfolio.direction,
        "cost_bps": portfolio.cost_bps,
    }


def delete_portfolio(session: Session, portfolio_id: int) -> dict:
    portfolio = writable_portfolio(session, portfolio_id)
    if portfolio.meta_set_id is not None:
        raise AdminOpError(
            409,
            "Meta portfolio members cannot be deleted individually; archive the member instead.",
        )
    session.delete(portfolio)
    session.commit()
    return {"ok": True}


def reset_portfolio(session: Session, portfolio_id: int) -> dict:
    """Delete the portfolio's mode-specific history while preserving identity,
    evaluator configuration, and evaluator audit rows."""
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

    deleted_allocations = 0
    deleted_signals = 0
    if portfolio.prompt_mode == "managed":
        deleted_allocations = int(
            session.scalar(
                select(func.count()).select_from(Allocation).where(Allocation.portfolio_id == portfolio.id)
            )
            or 0
        )
        session.execute(delete(Allocation).where(Allocation.portfolio_id == portfolio.id))
    else:
        deleted_signals = int(
            session.scalar(
                select(func.count()).select_from(Signal).where(Signal.portfolio_id == portfolio.id)
            )
            or 0
        )
        session.execute(delete(Signal).where(Signal.portfolio_id == portfolio.id))
    portfolio.founding_v2 = False
    session.commit()
    return {
        "ok": True,
        "deleted_allocations": deleted_allocations,
        "deleted_signals": deleted_signals,
        "cancelled_queued_runs": cancelled_queued_runs,
        "cancellation_requested_runs": cancellation_requested_runs,
    }


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
            if portfolio.prompt_mode == "rebuilt" and portfolio.direction == match.direction
        ]
        arena = compute_rebuilt_arena(
            session,
            same_direction,
            view="tuned",
            include_policy_matrix=True,
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
                view="tuned",
                horizon=None,
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
    if portfolio.prompt_mode != "managed":
        raise AdminOpError(409, "Rebuilt portfolios accept daily signals, not allocations.")
    if portfolio.status != "active":
        raise AdminOpError(409, "Unarchive the portfolio before adding allocations")
    _ensure_managed_not_liquidated(session, portfolio)

    policy = allocation_policy_out(get_app_settings(session), "managed")
    normalized = _normalize_positions(policy, positions)
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

    if positions is not None:
        if is_locked(allocation.effective_date, datetime.now(UTC)):
            raise AdminOpError(
                403,
                "Positions are frozen: the effective close has passed. Enter a new rebalance instead.",
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


def delete_allocation(session: Session, allocation_id: int) -> dict:
    allocation = session.scalars(
        select(Allocation).where(Allocation.id == allocation_id).options(selectinload(Allocation.portfolio))
    ).first()
    if allocation is None:
        raise AdminOpError(404, "Allocation not found")
    if is_locked(allocation.effective_date, datetime.now(UTC)):
        raise AdminOpError(403, "This allocation is locked: its effective close has passed.")
    session.delete(allocation)
    session.commit()
    return {"ok": True}


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
    """Create one independent rebuilt signal for the next future close."""
    portfolio = writable_portfolio(session, portfolio_id)
    if portfolio.prompt_mode != "rebuilt":
        raise AdminOpError(409, "Managed portfolios accept allocations, not daily signals.")
    if portfolio.status != "active":
        raise AdminOpError(409, "Unarchive the portfolio before adding signals.")
    if provenance not in {"browser_admin", "mcp"}:
        raise AdminOpError(422, "Only browser_admin or mcp provenance is accepted here.")

    current_time = now or datetime.now(UTC)
    effective = effective_date_for(current_time)
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
    if is_locked(signal.effective_date, current_time):
        raise AdminOpError(403, "This signal is immutable: its effective close has passed.")
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


def delete_signal(
    session: Session,
    signal_id: int,
    *,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    signal = session.scalars(select(Signal).where(Signal.id == signal_id).with_for_update()).first()
    if signal is None:
        raise AdminOpError(404, "Signal not found.")
    if is_locked(signal.effective_date, current_time):
        raise AdminOpError(403, "This signal is immutable: its effective close has passed.")
    session.delete(signal)
    session.commit()
    return {"ok": True}


# --- Settings ---------------------------------------------------------------


def _setting_value(session: Session, key: str, fallback: str) -> str:
    setting = session.get(Setting, key)
    return setting.value if setting is not None else fallback


def _setting_float(session: Session, key: str, fallback: float) -> float:
    return float(_setting_value(session, key, str(fallback)))


def get_app_settings(session: Session) -> dict:
    return {
        "default_cost_bps": _default_cost_bps(session),
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
    default_cost_bps: int,
    managed_allocation_policy: dict,
    rebuilt_allocation_policy: dict,
    managed_wrapper_prompt: str,
    rebuilt_wrapper_prompt: str,
    long_direction_instructions: str,
    short_direction_instructions: str,
) -> dict:
    if default_cost_bps < 0:
        raise AdminOpError(422, "Default cost bps cannot be negative")
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
        DEFAULT_COST_BPS_KEY: str(default_cost_bps),
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
