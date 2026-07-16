"""Lazy benchmark allocation seeding.

Benchmark portfolios (SPY/RSP buy & hold) get a single 100% allocation whose
effective date tracks the earliest real portfolio inception, so every
comparison starts from the same date and runs through the identical engine.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import BENCHMARKS
from ..models import Allocation, Portfolio, Position

logger = logging.getLogger(__name__)


def ensure_benchmark_allocations(session: Session) -> None:
    earliest = session.scalar(
        select(Allocation.effective_date)
        .join(Portfolio)
        .where(Portfolio.is_benchmark.is_(False))
        .order_by(Allocation.effective_date)
        .limit(1)
    )
    if earliest is None:
        return

    changed = False
    for benchmark in BENCHMARKS:
        portfolio = session.scalars(
            select(Portfolio)
            .where(Portfolio.slug == benchmark["slug"])
            .options(selectinload(Portfolio.allocations))
        ).first()
        if portfolio is None:
            continue

        if not portfolio.allocations:
            allocation = Allocation(
                portfolio_id=portfolio.id,
                entered_at=datetime.now(UTC),
                effective_date=earliest,
                note="System benchmark allocation.",
            )
            allocation.positions.append(
                Position(symbol=benchmark["symbol"], weight_pct=100)
            )
            session.add(allocation)
            changed = True
        else:
            first = portfolio.allocations[0]
            if first.effective_date > earliest:
                first.effective_date = earliest
                changed = True

    if changed:
        session.commit()
        logger.info("benchmark allocations aligned to inception %s", earliest)
