"""Idempotent seeding on every start: admin user, default settings, and the
benchmark agent/prompt/portfolios. Benchmark *allocations* are created lazily
(services/benchmarks.py) once the first real portfolio exists."""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import BENCHMARK_AGENT_SLUG, BENCHMARK_PROMPT_SLUG, BENCHMARKS, get_settings
from .models import Agent, Portfolio, Prompt, Setting, User
from .security import hash_password

logger = logging.getLogger(__name__)

DEFAULT_COST_BPS_KEY = "default_cost_bps"


def seed_users(session: Session) -> None:
    settings = get_settings()
    session.execute(
        pg_insert(User)
        .values(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
        .on_conflict_do_nothing(index_elements=["email"])
    )
    session.commit()


def seed_settings(session: Session) -> None:
    session.execute(
        pg_insert(Setting)
        .values(key=DEFAULT_COST_BPS_KEY, value=str(get_settings().default_cost_bps))
        .on_conflict_do_nothing(index_elements=["key"])
    )
    session.commit()


def seed_benchmarks(session: Session) -> None:
    agent = session.scalars(select(Agent).where(Agent.slug == BENCHMARK_AGENT_SLUG)).first()
    if agent is None:
        agent = Agent(slug=BENCHMARK_AGENT_SLUG, name="Benchmark", notes="System benchmark identity.")
        session.add(agent)
        session.flush()

    prompt = session.scalars(select(Prompt).where(Prompt.slug == BENCHMARK_PROMPT_SLUG)).first()
    if prompt is None:
        prompt = Prompt(
            slug=BENCHMARK_PROMPT_SLUG,
            name="Buy & Hold",
            text="Hold a single ETF forever. System benchmark, not an AI prompt.",
            notes="System prompt for benchmark portfolios.",
        )
        session.add(prompt)
        session.flush()

    for benchmark in BENCHMARKS:
        existing = session.scalars(select(Portfolio).where(Portfolio.slug == benchmark["slug"])).first()
        if existing is None:
            session.add(
                Portfolio(
                    slug=benchmark["slug"],
                    name=benchmark["name"],
                    agent_id=agent.id,
                    cost_bps=0,
                    is_benchmark=True,
                )
            )
    session.commit()


def run_seed(session: Session) -> None:
    seed_users(session)
    seed_settings(session)
    seed_benchmarks(session)
