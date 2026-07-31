"""Idempotent seeding of application settings on every start."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EvaluatorSettings, Setting
from .services.prompt_policy import DEFAULT_MANAGED_WRAPPER_PROMPT, DEFAULT_REBUILT_WRAPPER_PROMPT

DEFAULT_COST_BPS_KEY = "default_cost_bps"
MANAGED_WRAPPER_PROMPT_KEY = "managed_wrapper_prompt"
REBUILT_WRAPPER_PROMPT_KEY = "rebuilt_wrapper_prompt"


def seed_settings(session: Session) -> None:
    defaults = {
        DEFAULT_COST_BPS_KEY: str(get_settings().default_cost_bps),
        MANAGED_WRAPPER_PROMPT_KEY: DEFAULT_MANAGED_WRAPPER_PROMPT,
        REBUILT_WRAPPER_PROMPT_KEY: DEFAULT_REBUILT_WRAPPER_PROMPT,
    }
    for key, value in defaults.items():
        session.execute(
            pg_insert(Setting).values(key=key, value=value).on_conflict_do_nothing(index_elements=["key"])
        )
    session.execute(pg_insert(EvaluatorSettings).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    session.commit()


def run_seed(session: Session) -> None:
    seed_settings(session)
