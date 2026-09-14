"""Idempotent seeding of application settings on every start."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import ArenaVersion, EvaluatorSettings, Setting, SettingPromptVersion
from .services.prompt_policy import (
    DEFAULT_EXECUTION_INSTRUCTIONS,
    DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
    DEFAULT_MANAGED_WRAPPER_PROMPT,
    DEFAULT_REBUILT_WRAPPER_PROMPT,
    DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
)
from .settings_prompt_baseline import SETTINGS_PROMPT_BASELINE

MANAGED_WRAPPER_PROMPT_KEY = "managed_wrapper_prompt"
REBUILT_WRAPPER_PROMPT_KEY = "rebuilt_wrapper_prompt"
LONG_DIRECTION_INSTRUCTIONS_KEY = "long_direction_instructions"
SHORT_DIRECTION_INSTRUCTIONS_KEY = "short_direction_instructions"
MANAGED_MIN_POSITION_WEIGHT_PCT_KEY = "managed_min_position_weight_pct"
MANAGED_MAX_POSITION_WEIGHT_PCT_KEY = "managed_max_position_weight_pct"
REBUILT_MIN_POSITION_WEIGHT_PCT_KEY = "rebuilt_min_position_weight_pct"
REBUILT_MAX_POSITION_WEIGHT_PCT_KEY = "rebuilt_max_position_weight_pct"


SETTINGS_PROMPT_DEFAULTS = {
    **DEFAULT_EXECUTION_INSTRUCTIONS,
    MANAGED_WRAPPER_PROMPT_KEY: DEFAULT_MANAGED_WRAPPER_PROMPT,
    REBUILT_WRAPPER_PROMPT_KEY: DEFAULT_REBUILT_WRAPPER_PROMPT,
    LONG_DIRECTION_INSTRUCTIONS_KEY: DEFAULT_LONG_DIRECTION_INSTRUCTIONS,
    SHORT_DIRECTION_INSTRUCTIONS_KEY: DEFAULT_SHORT_DIRECTION_INSTRUCTIONS,
}


def seed_settings(session: Session) -> None:
    defaults = {
        **SETTINGS_PROMPT_DEFAULTS,
        MANAGED_MIN_POSITION_WEIGHT_PCT_KEY: "10",
        MANAGED_MAX_POSITION_WEIGHT_PCT_KEY: "25",
        REBUILT_MIN_POSITION_WEIGHT_PCT_KEY: "10",
        REBUILT_MAX_POSITION_WEIGHT_PCT_KEY: "100",
    }
    for key, value in sorted(defaults.items()):
        session.execute(
            pg_insert(Setting).values(key=key, value=value).on_conflict_do_nothing(index_elements=["key"])
        )
    for key in sorted(SETTINGS_PROMPT_DEFAULTS):
        setting = session.scalars(select(Setting).where(Setting.key == key).with_for_update()).one()
        for version, snapshot in ((1, SETTINGS_PROMPT_BASELINE[key]), (2, setting.value)):
            session.execute(
                pg_insert(SettingPromptVersion)
                .values(key=key, version=version, text=snapshot)
                .on_conflict_do_nothing(index_elements=["key", "version"])
            )
    session.execute(pg_insert(EvaluatorSettings).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    session.commit()


def run_seed(session: Session) -> None:
    seed_settings(session)
    if session.scalar(select(ArenaVersion.id).limit(1)) is None:
        session.add(ArenaVersion(name="v1", evaluation_enabled=False))
        session.commit()
