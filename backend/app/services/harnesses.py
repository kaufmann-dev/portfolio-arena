"""Code-defined execution harness capabilities.

Harnesses require application and worker support, so they are registered in
code rather than managed as database rows. Models choose fixed reasoning efforts
or declare provider-specific variants for harnesses with custom effort controls.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ReasoningEffort:
    id: str
    name: str


@dataclass(frozen=True)
class HarnessDefinition:
    id: str
    name: str
    automation_supported: bool
    reasoning_effort_mode: Literal["fixed", "custom"]
    reasoning_efforts: tuple[ReasoningEffort, ...]


CODEX = HarnessDefinition(
    id="codex",
    name="Codex",
    automation_supported=True,
    reasoning_effort_mode="fixed",
    reasoning_efforts=(
        ReasoningEffort("low", "Low"),
        ReasoningEffort("medium", "Medium"),
        ReasoningEffort("high", "High"),
        ReasoningEffort("xhigh", "Extra high"),
        ReasoningEffort("ultra", "Ultra"),
        ReasoningEffort("max", "Max"),
    ),
)

MUSE = HarnessDefinition(
    id="muse",
    name="Muse Code",
    automation_supported=True,
    reasoning_effort_mode="fixed",
    reasoning_efforts=(
        ReasoningEffort("none", "None"),
        ReasoningEffort("minimal", "Minimal"),
        ReasoningEffort("low", "Low"),
        ReasoningEffort("medium", "Medium"),
        ReasoningEffort("high", "High"),
        ReasoningEffort("xhigh", "Extra high"),
        ReasoningEffort("max", "Max"),
        ReasoningEffort("ultra", "Ultra"),
    ),
)

OPENCODE = HarnessDefinition(
    id="opencode",
    name="OpenCode",
    automation_supported=True,
    reasoning_effort_mode="custom",
    reasoning_efforts=(),
)

AGY = HarnessDefinition(
    id="agy",
    name="Antigravity",
    automation_supported=True,
    reasoning_effort_mode="fixed",
    reasoning_efforts=(
        ReasoningEffort("low", "Low"),
        ReasoningEffort("medium", "Medium"),
        ReasoningEffort("high", "High"),
    ),
)

HARNESS_DEFINITIONS = {harness.id: harness for harness in (CODEX, MUSE, OPENCODE, AGY)}


def get_harness(harness_id: str) -> HarnessDefinition | None:
    return HARNESS_DEFINITIONS.get(harness_id)


def supports_automation(harness_id: str | None) -> bool:
    harness = get_harness(harness_id) if harness_id else None
    return bool(harness and harness.automation_supported)


def automation_harness_ids() -> tuple[str, ...]:
    return tuple(harness.id for harness in HARNESS_DEFINITIONS.values() if harness.automation_supported)


def harnesses_out() -> dict:
    return {
        "harnesses": [
            {
                "id": harness.id,
                "name": harness.name,
                "automation_supported": harness.automation_supported,
                "reasoning_effort_mode": harness.reasoning_effort_mode,
                "reasoning_efforts": [
                    {"id": effort.id, "name": effort.name} for effort in harness.reasoning_efforts
                ],
            }
            for harness in HARNESS_DEFINITIONS.values()
        ]
    }
