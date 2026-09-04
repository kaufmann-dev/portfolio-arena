"""Model catalog validation and execution-profile response shaping."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, ModelDefinition, ModelHarnessCapability
from .errors import AdminOpError
from .harnesses import get_harness


def capability_out(capability: ModelHarnessCapability) -> dict:
    harness = get_harness(capability.harness)
    return {
        "harness": capability.harness,
        "harness_name": harness.name if harness else capability.harness,
        "execution_model_id": capability.execution_model_id,
        "reasoning_efforts": list(capability.reasoning_efforts),
    }


def model_ref(model: ModelDefinition) -> dict:
    return {"id": model.id, "slug": model.slug, "name": model.name}


def model_out(model: ModelDefinition, *, agent_count: int | None = None) -> dict:
    result = {
        **model_ref(model),
        "notes": model.notes,
        "capabilities": [capability_out(capability) for capability in model.capabilities],
        "created_at": model.created_at.isoformat(),
        "updated_at": model.updated_at.isoformat(),
    }
    if agent_count is not None:
        result["agent_count"] = agent_count
    return result


def execution_profile_name(
    model: ModelDefinition,
    harness_id: str | None,
    reasoning_effort: str | None,
) -> str:
    if harness_id is None:
        return f"{model.name} (No supported harness)"
    harness = get_harness(harness_id)
    harness_name = harness.name if harness else harness_id
    if reasoning_effort is None:
        return f"{model.name} ({harness_name})"
    effort_name = next(
        (
            effort.name
            for effort in (harness.reasoning_efforts if harness else ())
            if effort.id == reasoning_effort
        ),
        reasoning_effort,
    )
    return f"{model.name} ({harness_name}, {effort_name})"


def agent_name(agent: Agent) -> str:
    return execution_profile_name(agent.model, agent.harness, agent.reasoning_effort)


def agent_out(agent: Agent, *, portfolio_count: int | None = None) -> dict:
    capability = next(
        (capability for capability in agent.model.capabilities if capability.harness == agent.harness),
        None,
    )
    harness = get_harness(agent.harness) if agent.harness else None
    result = {
        "id": agent.id,
        "slug": agent.slug,
        "name": agent_name(agent),
        "notes": agent.notes,
        "model": model_ref(agent.model),
        "harness": (
            {"id": agent.harness, "name": harness.name if harness else agent.harness}
            if agent.harness
            else None
        ),
        "execution_model_id": capability.execution_model_id if capability else None,
        "reasoning_effort": agent.reasoning_effort,
        "status": agent.status,
        "archived_at": agent.archived_at.isoformat() if agent.archived_at is not None else None,
    }
    if portfolio_count is not None:
        result["portfolio_count"] = portfolio_count
    return result


def agent_snapshot_out(
    agent: Agent,
    model: ModelDefinition,
    *,
    harness: str,
    execution_model_id: str,
    reasoning_effort: str | None,
) -> dict:
    harness_definition = get_harness(harness)
    return {
        "id": agent.id,
        "slug": agent.slug,
        "name": execution_profile_name(model, harness, reasoning_effort),
        "notes": agent.notes,
        "model": model_ref(model),
        "harness": {
            "id": harness,
            "name": harness_definition.name if harness_definition else harness,
        },
        "execution_model_id": execution_model_id,
        "reasoning_effort": reasoning_effort,
    }


def load_agent(session: Session, agent_id: int, *, lock: bool = False) -> Agent | None:
    query = (
        select(Agent)
        .where(Agent.id == agent_id)
        .options(selectinload(Agent.model).selectinload(ModelDefinition.capabilities))
    )
    if lock:
        query = query.with_for_update()
    return session.scalars(query).first()


def validate_capabilities(capabilities: list[dict]) -> list[dict]:
    seen_harnesses: set[str] = set()
    normalized = []
    for capability in capabilities:
        harness_id = capability["harness"].strip()
        harness = get_harness(harness_id)
        if harness is None:
            raise AdminOpError(422, f"Unsupported harness: {harness_id or '(empty)'}")
        if harness_id in seen_harnesses:
            raise AdminOpError(422, f"Harness {harness_id} may appear only once")
        seen_harnesses.add(harness_id)
        execution_model_id = capability["execution_model_id"].strip()
        if not execution_model_id:
            raise AdminOpError(422, "Execution model ID is required")
        efforts = capability["reasoning_efforts"]
        if len(efforts) != len(set(efforts)):
            raise AdminOpError(422, f"Reasoning efforts for {harness.name} must be unique")
        allowed = {effort.id for effort in harness.reasoning_efforts}
        invalid = [effort for effort in efforts if effort not in allowed]
        if invalid:
            raise AdminOpError(
                422,
                f"Unsupported reasoning effort for {harness.name}: {invalid[0]}",
            )
        normalized.append(
            {
                "harness": harness_id,
                "execution_model_id": execution_model_id,
                "reasoning_efforts": efforts,
            }
        )
    return normalized


def validate_agent_profile(
    session: Session,
    *,
    model_id: int,
    harness: str | None,
    reasoning_effort: str | None,
) -> tuple[ModelDefinition, ModelHarnessCapability | None, str | None]:
    model = session.scalars(
        select(ModelDefinition)
        .where(ModelDefinition.id == model_id)
        .options(selectinload(ModelDefinition.capabilities))
    ).first()
    if model is None:
        raise AdminOpError(422, "Model not found")
    clean_harness = harness.strip() if harness else None
    clean_effort = reasoning_effort.strip() if reasoning_effort else None
    if clean_harness is None:
        if clean_effort is not None:
            raise AdminOpError(422, "Reasoning effort requires a supported harness")
        return model, None, None
    if get_harness(clean_harness) is None:
        raise AdminOpError(422, f"Unsupported harness: {clean_harness}")
    capability = next(
        (item for item in model.capabilities if item.harness == clean_harness),
        None,
    )
    if capability is None:
        raise AdminOpError(422, f"{model.name} is not configured for {clean_harness}")
    efforts = list(capability.reasoning_efforts)
    if efforts and clean_effort is None:
        raise AdminOpError(422, "Select a reasoning effort for this model and harness")
    if not efforts and clean_effort is not None:
        raise AdminOpError(422, "This model and harness do not expose reasoning effort")
    if clean_effort is not None and clean_effort not in efforts:
        raise AdminOpError(422, f"Unsupported reasoning effort: {clean_effort}")
    return model, capability, clean_effort
