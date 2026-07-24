"""Portfolio reset removes allocation/performance state without replacing the portfolio."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.services import evaluator
from app.services.admin_ops import AdminOpError

from .util import backdate_allocation


def _allocation_body(symbol: str = "AAPL") -> dict:
    return {
        "positions": [{"symbol": symbol, "weight_pct": 100}],
        "note": "fresh start",
    }


def _enable_automation(session, portfolio_id: int) -> None:
    evaluator.update_portfolio_config(
        session,
        portfolio_id=portfolio_id,
        enabled=True,
        weekdays=[],
    )


def test_reset_removes_locked_and_pending_history_and_allows_fresh_start(
    client,
    admin_headers,
    sample_portfolio,
):
    backdate_allocation(sample_portfolio["allocation"]["id"])
    pending = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/allocations",
        json=_allocation_body("MSFT"),
        headers=admin_headers,
    )
    assert pending.status_code == 201, pending.text

    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "deleted_allocations": 2,
        "cancelled_queued_runs": 0,
        "cancellation_requested_runs": 0,
    }
    detail = client.get(
        f"/api/portfolios/{sample_portfolio['id']}/detail",
        headers=admin_headers,
    ).json()["portfolio"]
    assert detail["allocation_count"] == 0
    assert detail["allocations"] == []
    assert detail["holdings"] == []
    assert detail["series"] == []
    assert detail["inception"] is None
    assert detail["metrics"]["has_data"] is False

    first = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/allocations",
        json=_allocation_body(),
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["turnover_pct"] is None


def test_reset_is_idempotent_admin_only_and_rejects_benchmarks(
    client,
    admin_headers,
    sample_portfolio,
):
    assert client.post(f"/api/portfolios/{sample_portfolio['id']}/reset").status_code == 401

    first = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    second = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert first.json()["deleted_allocations"] == 1
    assert second.json()["deleted_allocations"] == 0

    benchmark = next(
        row for row in client.get("/api/leaderboard").json()["portfolios"] if row["is_benchmark"]
    )
    assert (
        client.post(
            f"/api/portfolios/{benchmark['id']}/reset",
            headers=admin_headers,
        ).status_code
        == 403
    )
    assert client.post("/api/portfolios/999999/reset", headers=admin_headers).status_code == 404


def test_reset_last_contestant_history_clears_benchmarks(
    client,
    admin_headers,
    sample_portfolio,
):
    backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
    seeded = client.get("/api/leaderboard").json()["portfolios"]
    assert all(row["allocation_count"] == 1 for row in seeded if row["is_benchmark"])

    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    benchmarks = [row for row in client.get("/api/leaderboard").json()["portfolios"] if row["is_benchmark"]]
    assert all(row["allocation_count"] == 0 for row in benchmarks)
    assert all(row["inception"] is None for row in benchmarks)
    assert all(row["metrics"]["has_data"] is False for row in benchmarks)


def test_reset_keeps_automation_enabled_and_cancels_queued_work(
    client,
    admin_headers,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import EvaluationRun, PortfolioEvaluatorConfig

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable_automation(session, sample_portfolio["id"])
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]

    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["cancelled_queued_runs"] == 1

    with session_factory()() as session:
        config = session.get(PortfolioEvaluatorConfig, sample_portfolio["id"])
        run = session.get(EvaluationRun, run_id)
        assert config.enabled is True
        assert run.status == "cancelled"
        assert run.error == "Cancelled because the portfolio was reset."


def test_reset_stops_running_submission_from_recreating_an_allocation(
    client,
    admin_headers,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import Allocation, EvaluationRun

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable_automation(session, sample_portfolio["id"])
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="codex-cli 0.144.5",
            limit=1,
            now=now,
        )

    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["cancellation_requested_runs"] == 1

    with session_factory()() as session:
        with pytest.raises(AdminOpError, match="cancelled"):
            evaluator.submit_run(
                session,
                run_id=run_id,
                positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "stale run"}],
                note="must not land",
                report="must not land",
                now=now + timedelta(minutes=5),
            )
        assert session.get(EvaluationRun, run_id).status == "cancelled"
        assert (
            session.scalars(select(Allocation).where(Allocation.portfolio_id == sample_portfolio["id"])).all()
            == []
        )


def test_reset_preserves_succeeded_run_audit_with_empty_allocation_link(
    client,
    admin_headers,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import EvaluationRun

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable_automation(session, sample_portfolio["id"])
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="codex-cli 0.144.5",
            limit=1,
            now=now,
        )
        evaluator.submit_run(
            session,
            run_id=run_id,
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "new thesis"}],
            note="completed run",
            report="audit report",
            now=now + timedelta(minutes=5),
        )

    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["deleted_allocations"] == 2

    with session_factory()() as session:
        run = session.get(EvaluationRun, run_id)
        assert run.status == "succeeded"
        assert run.allocation_id is None
        assert run.report == "audit report"
