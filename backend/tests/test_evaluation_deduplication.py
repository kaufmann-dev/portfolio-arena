"""Manual, scheduled and retry evaluations share portfolio and session guards."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import session_factory
from app.models import EvaluationRun, Portfolio
from app.services import admin_ops, evaluator
from app.services.admin_ops import AdminOpError
from app.services.trading_calendar import boundary_at, effective_date_for


def _portfolio(session, agent, prompt, mode, boundary="close", name="Deduplication"):
    portfolio = admin_ops.create_portfolio(
        session,
        name=name,
        agent_id=agent["id"],
        prompt_id=prompt["id"],
        prompt_mode=mode,
        direction="long",
        execution_boundary=boundary,
        version_id=1,
    )
    evaluator.update_portfolio_config(
        session, portfolio_id=portfolio["id"], enabled=True, weekdays=[0, 1, 2, 3, 4]
    )
    return portfolio


def _claim(session, now, worker="worker", limit=1):
    return evaluator.claim_runs(
        session, worker_id=worker, harness="codex", harness_version="test", limit=limit, now=now
    )["runs"]


def _decision(session, portfolio, now, effective):
    positions = [{"symbol": "AAPL", "weight_pct": 100, "note": "Evidence"}]
    record = session.get(Portfolio, portfolio["id"])
    decision = (
        admin_ops._new_allocation(record, positions, "Manual decision", now, effective)
        if record.prompt_mode == "managed"
        else admin_ops._new_signal(record, positions, "Manual decision", now, effective, "browser_admin")
    )
    session.add(decision)
    session.commit()


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("boundary", ["open", "close"])
@pytest.mark.parametrize("abstain", [False, True])
def test_completed_manual_evaluation_satisfies_schedule(sample_agent, sample_prompt, mode, boundary, abstain):
    now = boundary_at(date(2026, 7, 20), boundary) - timedelta(minutes=30)
    with session_factory()() as session:
        portfolio = _portfolio(session, sample_agent, sample_prompt, mode, boundary)
        queued = evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=now)
        run_id = queued["items"][0]["run"]["id"]
        assert [run["id"] for run in _claim(session, now)] == [run_id]
        assert _claim(session, now, worker="other-worker") == []
        evaluator.submit_run(
            session,
            run_id=run_id,
            attempt_count=1,
            positions=[] if abstain else [{"symbol": "AAPL", "weight_pct": 100}],
            note="Research completed",
            report="No qualifying opportunity" if abstain else "Supported opportunity",
            now=now + timedelta(minutes=1),
        )
        assert _claim(session, now + timedelta(minutes=2)) == []
        assert [run.id for run in session.scalars(select(EvaluationRun))] == [run_id]
        # A decision for this session must not suppress tomorrow's evaluation.
        following = _claim(session, now + timedelta(days=1))
        assert len(following) == 1
        assert following[0]["trigger_kind"] == "scheduled"


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("boundary", ["open", "close"])
@pytest.mark.parametrize("trigger", ["manual", "scheduled", "retry"])
def test_existing_decision_skips_queued_work_before_claim_and_refills_capacity(
    sample_agent, sample_prompt, mode, boundary, trigger
):
    day = date(2026, 7, 20)
    now = boundary_at(day, boundary) - timedelta(minutes=30)
    after_boundary = boundary_at(day, boundary) + timedelta(minutes=1)
    with session_factory()() as session:
        duplicate = _portfolio(session, sample_agent, sample_prompt, mode, boundary)
        if trigger == "scheduled":
            assert _claim(session, now, limit=0) == []
            run = session.scalars(select(EvaluationRun)).one()
        else:
            queued = evaluator.enqueue_manual_runs(session, portfolio_ids=[duplicate["id"]], now=now)
            run = session.get(EvaluationRun, queued["items"][0]["run"]["id"])
            if trigger == "retry":
                _claim(session, now)
                evaluator.fail_run(session, run_id=run.id, attempt_count=1, error="Transient", now=now)
        attempt_count = run.attempt_count
        effective = day if trigger == "scheduled" else effective_date_for(after_boundary, boundary)
        _decision(session, duplicate, now, effective)
        other = _portfolio(session, sample_agent, sample_prompt, mode, boundary, name="Other portfolio")
        evaluator.enqueue_manual_runs(session, portfolio_ids=[other["id"]], now=after_boundary)

        claimed = _claim(session, after_boundary)
        assert [item["portfolio"]["id"] for item in claimed] == [other["id"]]
        session.refresh(run)
        assert run.status == "skipped"
        assert run.attempt_count == attempt_count
        assert run.worker_id is None and run.lease_expires_at is None
        assert run.finished_at == after_boundary


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("decision_day_offset", [0, 1])
def test_scheduled_retry_checks_original_session(sample_agent, sample_prompt, mode, decision_day_offset):
    day = date(2026, 7, 20)
    now = boundary_at(day, "close") - timedelta(minutes=30)
    after_close = boundary_at(day, "close") + timedelta(minutes=1)
    with session_factory()() as session:
        portfolio = _portfolio(session, sample_agent, sample_prompt, mode)
        evaluator.update_settings(session, max_attempts=1)
        source = _claim(session, now)[0]
        evaluator.fail_run(session, run_id=source["id"], attempt_count=1, error="Failed", now=now)
        _decision(session, portfolio, now, day + timedelta(days=decision_day_offset))
        if decision_day_offset == 0:
            with pytest.raises(AdminOpError, match="result already targets this effective session"):
                evaluator.retry_run(session, run_id=source["id"], now=after_close)
        else:
            retried = evaluator.retry_run(session, run_id=source["id"], now=after_close)
            assert retried["run"]["scheduled_for"] == day.isoformat()
            assert [run["id"] for run in _claim(session, after_close)] == [retried["run"]["id"]]


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
def test_manual_retry_and_two_schedulers_concurrently_share_one_active_run(sample_agent, sample_prompt, mode):
    now = boundary_at(date(2026, 7, 20), "close") - timedelta(minutes=30)
    earlier = now - timedelta(hours=2)
    with session_factory()() as session:
        portfolio = _portfolio(session, sample_agent, sample_prompt, mode)
        evaluator.update_settings(session, max_attempts=1, max_concurrency=8)
        evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=earlier)
        source = _claim(session, earlier)[0]
        evaluator.fail_run(session, run_id=source["id"], attempt_count=1, error="Failed", now=earlier)

    ready = Barrier(4)

    def request(action):
        with session_factory()() as session:
            ready.wait(timeout=10)
            if action == "manual":
                return evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=now)
            if action == "retry":
                return evaluator.retry_run(session, run_id=source["id"], now=now)
            return _claim(session, now, worker=action)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(request, ["manual", "retry", "worker-1", "worker-2"]))
    claimed = results[2] + results[3]
    assert len(claimed) == 1
    run_id = claimed[0]["id"]
    assert results[0]["items"][0]["run"]["id"] == results[1]["run"]["id"] == run_id
    with session_factory()() as session:
        active = session.scalars(
            select(EvaluationRun).where(EvaluationRun.status.in_(evaluator.ACTIVE_STATUSES))
        ).all()
        assert [run.id for run in active] == [run_id]
        assert active[0].attempt_count == 1
        evaluator.cancel_run(session, run_id=run_id, now=now)
        # Pending cancellation still owns the slot until its worker stops.
        assert _claim(session, now, worker="worker-3") == []
        assert (
            evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=now)["items"][0][
                "action"
            ]
            == "existing"
        )
        assert evaluator.retry_run(session, run_id=source["id"], now=now)["action"] == "existing"


@pytest.mark.parametrize("status", ["queued", "running", "cancel_requested"])
def test_database_rejects_second_active_run(sample_agent, sample_prompt, status):
    now = boundary_at(date(2026, 7, 20), "close") - timedelta(minutes=30)
    with session_factory()() as session:
        portfolio = _portfolio(session, sample_agent, sample_prompt, "rebuilt")
        queued = evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=now)
        run = session.get(EvaluationRun, queued["items"][0]["run"]["id"])
        run.status = status
        session.commit()
        # The constraint protects the invariant even if a caller bypasses the queue service.
        session.add(
            evaluator._new_run(
                run.portfolio.evaluator_config, evaluator.get_settings(session), trigger_kind="manual"
            )
        )
        with pytest.raises(IntegrityError, match="evaluation_runs_portfolio_active_key"):
            session.flush()
        session.rollback()
