"""Idempotent seeding on every start: default settings and benchmark
portfolios. Benchmark *allocations* are created lazily
(services/benchmarks.py) once the first real portfolio exists."""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import BENCHMARKS, get_settings
from .models import EvaluatorSettings, Portfolio, Setting

logger = logging.getLogger(__name__)

DEFAULT_COST_BPS_KEY = "default_cost_bps"


def seed_settings(session: Session) -> None:
    session.execute(
        pg_insert(Setting)
        .values(key=DEFAULT_COST_BPS_KEY, value=str(get_settings().default_cost_bps))
        .on_conflict_do_nothing(index_elements=["key"])
    )
    session.execute(pg_insert(EvaluatorSettings).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    session.commit()


def seed_benchmarks(session: Session) -> None:
    for benchmark in BENCHMARKS:
        existing = session.scalars(select(Portfolio).where(Portfolio.slug == benchmark["slug"])).first()
        if existing is None:
            session.add(
                Portfolio(
                    slug=benchmark["slug"],
                    name=benchmark["name"],
                    agent_id=None,
                    prompt_id=None,
                    cost_bps=0,
                    is_benchmark=True,
                )
            )
    session.commit()


def run_seed(session: Session) -> None:
    seed_settings(session)
    seed_benchmarks(session)
