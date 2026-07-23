"""Benchmark allocation reconciliation.

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

BENCHMARK_ALLOCATION_NOTE = "System benchmark allocation."


def reconcile_benchmark_allocations(session: Session) -> bool:
    """Make benchmark allocations match the surviving contestant history.

    The benchmark portfolio locks serialize concurrent reconcilers. Callers own
    the transaction so contestant mutations and benchmark changes commit
    atomically.
    """
    portfolios = session.scalars(
        select(Portfolio)
        .where(Portfolio.is_benchmark.is_(True))
        .order_by(Portfolio.id)
        .with_for_update()
        .options(selectinload(Portfolio.allocations).selectinload(Allocation.positions))
    ).all()
    portfolios_by_slug = {portfolio.slug: portfolio for portfolio in portfolios}

    earliest = session.scalar(
        select(Allocation.effective_date)
        .join(Portfolio)
        .where(Portfolio.is_benchmark.is_(False))
        .order_by(Allocation.effective_date, Allocation.id)
        .limit(1)
    )

    changed = False
    for benchmark in BENCHMARKS:
        portfolio = portfolios_by_slug.get(benchmark["slug"])
        if portfolio is None:
            continue

        if earliest is None:
            for allocation in portfolio.allocations:
                session.delete(allocation)
                changed = True
            continue

        allocations = list(portfolio.allocations)
        if not allocations:
            allocation = Allocation(
                portfolio_id=portfolio.id,
                entered_at=datetime.now(UTC),
                effective_date=earliest,
                note=BENCHMARK_ALLOCATION_NOTE,
            )
            allocation.positions.append(Position(symbol=benchmark["symbol"], weight_pct=100))
            session.add(allocation)
            changed = True
        else:
            primary = allocations[0]
            for duplicate in allocations[1:]:
                session.delete(duplicate)
                changed = True

            if primary.effective_date != earliest:
                primary.effective_date = earliest
                changed = True
            if primary.note != BENCHMARK_ALLOCATION_NOTE:
                primary.note = BENCHMARK_ALLOCATION_NOTE
                changed = True

            positions_are_standard = (
                len(primary.positions) == 1
                and primary.positions[0].symbol == benchmark["symbol"]
                and primary.positions[0].weight_pct == 100
            )
            if not positions_are_standard:
                primary.positions.clear()
                session.flush()
                primary.positions.append(Position(symbol=benchmark["symbol"], weight_pct=100))
                changed = True

    if changed:
        session.flush()
        logger.info("benchmark allocations reconciled to inception %s", earliest)
    return changed
