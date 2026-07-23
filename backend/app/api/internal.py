"""Loopback evaluator endpoints authenticated by an ephemeral production token."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import (
    EvaluatorClaimIn,
    EvaluatorHeartbeatIn,
    EvaluatorRunFailIn,
    EvaluatorRunSubmitIn,
)
from ..security import require_internal_worker
from ..services import evaluator
from ..services.admin_ops import AdminOpError

router = APIRouter(
    prefix="/api/internal/evaluator",
    dependencies=[Depends(require_internal_worker)],
)


def _run[T](fn: Callable[..., T], *args, **kwargs) -> T:
    try:
        return fn(*args, **kwargs)
    except AdminOpError as exc:
        raise HTTPException(exc.status_code, exc.message) from None


def _positions(body: EvaluatorRunSubmitIn) -> list[dict]:
    return [
        {
            "symbol": position.symbol,
            "weight_pct": position.weight_pct,
            "note": position.note,
        }
        for position in body.positions
    ]


@router.post("/heartbeat")
def heartbeat(body: EvaluatorHeartbeatIn, session: Session = Depends(get_session)):
    return evaluator.heartbeat(session, **body.model_dump())


@router.post("/claim")
def claim(body: EvaluatorClaimIn, session: Session = Depends(get_session)):
    return evaluator.claim_runs(session, **body.model_dump())


@router.get("/runs/{run_id}/control")
def run_control(run_id: int, session: Session = Depends(get_session)):
    return _run(evaluator.run_control, session, run_id=run_id)


@router.post("/runs/{run_id}/submit")
def submit_run(
    run_id: int,
    body: EvaluatorRunSubmitIn,
    session: Session = Depends(get_session),
):
    return _run(
        evaluator.submit_run,
        session,
        run_id=run_id,
        positions=_positions(body),
        note=body.note,
        report=body.report,
    )


@router.post("/runs/{run_id}/fail")
def fail_run(
    run_id: int,
    body: EvaluatorRunFailIn,
    session: Session = Depends(get_session),
):
    return _run(
        evaluator.fail_run,
        session,
        run_id=run_id,
        error=body.error,
        cancelled=body.cancelled,
    )
