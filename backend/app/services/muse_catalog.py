"""Import models and declared reasoning settings from the Muse Code catalog."""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import ModelDefinition, ModelHarnessCapability
from ..util import slugify
from .errors import AdminOpError
from .harnesses import MUSE


@dataclass(frozen=True)
class MuseCatalogModel:
    execution_model_id: str
    name: str
    notes: str
    reasoning_efforts: tuple[str, ...]


def parse_muse_catalog(data: list[dict]) -> list[MuseCatalogModel]:
    """Read the provider's /muse-code/models metadata, without inventing variants.

    Muse Code 1.1.1 reads the OpenAI-style ``data`` envelope and the
    ``metadata["muse-code"]`` extension. It normalizes the keys of object-valued
    ``variants`` entries into its ``reasoning_effort_variants[].tier`` field.
    Missing variants leave reasoning to the CLI's default.
    """
    result = []
    seen = set()
    for row in data:
        execution_id = row.get("id")
        metadata = row.get("metadata")
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise AdminOpError(422, "Muse catalog contains a model without an execution ID.")
        if not isinstance(metadata, dict):
            continue
        muse = metadata.get("muse-code")
        if not isinstance(muse, dict) or muse.get("is_hidden") is True:
            continue
        if muse.get("tool_call") is False:
            continue
        execution_id = execution_id.strip()
        if execution_id in seen:
            raise AdminOpError(422, "Muse catalog contains duplicate execution IDs.")
        seen.add(execution_id)

        variants = muse.get("variants", {})
        if not isinstance(variants, dict):
            raise AdminOpError(422, "Muse catalog contains malformed reasoning variants.")
        declared = {effort for effort, variant in variants.items() if isinstance(variant, dict)}

        name = muse.get("name")
        description = muse.get("description")
        result.append(
            MuseCatalogModel(
                execution_model_id=execution_id,
                name=name.strip() if isinstance(name, str) and name.strip() else execution_id,
                notes=description.strip() if isinstance(description, str) else "",
                reasoning_efforts=tuple(
                    effort.id for effort in MUSE.reasoning_efforts if effort.id in declared
                ),
            )
        )
    if not result:
        raise AdminOpError(422, "Muse catalog contains no visible models with Muse Code metadata.")
    return result


def import_muse_models(session: Session, *, data: list[dict]) -> dict:
    """Add discovered models without replacing administrator-maintained entries."""
    discovered = parse_muse_catalog(data)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('muse-model-catalog-import'))"))
    models_added = 0
    capabilities_added = 0
    for model in discovered:
        known_execution = session.scalar(
            select(ModelHarnessCapability.model_id).where(
                ModelHarnessCapability.harness == MUSE.id,
                ModelHarnessCapability.execution_model_id == model.execution_model_id,
            )
        )
        if known_execution is not None:
            continue

        slug = slugify(model.execution_model_id)
        definition = session.scalar(select(ModelDefinition).where(ModelDefinition.slug == slug))
        if definition is None:
            definition = ModelDefinition(slug=slug, name=model.name, notes=model.notes)
            session.add(definition)
            session.flush()
            models_added += 1
        elif session.get(ModelHarnessCapability, (definition.id, MUSE.id)) is not None:
            continue

        session.add(
            ModelHarnessCapability(
                model_id=definition.id,
                harness=MUSE.id,
                execution_model_id=model.execution_model_id,
                reasoning_efforts=list(model.reasoning_efforts),
            )
        )
        session.flush()
        capabilities_added += 1
    session.commit()
    return {
        "models_seen": len(discovered),
        "models_added": models_added,
        "capabilities_added": capabilities_added,
    }
