"""Integrated evaluator settings, queue lifecycle, and admin controls."""

from datetime import UTC, date, datetime, timedelta

from app.services import admin_ops, evaluator
from app.services.trading_calendar import close_at, effective_date_for

from .util import backdate_allocation


def _enable(session, portfolio, weekdays=None):
    return evaluator.update_portfolio_config(
        session,
        portfolio_id=portfolio["id"],
        enabled=True,
        weekdays=weekdays or [],
    )


def test_manual_run_claim_and_submission_use_submission_effective_date(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        app_settings = admin_ops.get_app_settings(session)
        managed_wrapper = app_settings["managed_wrapper_prompt"].replace(
            "Evaluate the Portfolio Arena portfolio",
            "Managed wrapper marker for",
        )
        admin_ops.update_app_settings(
            session,
            default_cost_bps=app_settings["default_cost_bps"],
            managed_wrapper_prompt=managed_wrapper,
            rebuilt_wrapper_prompt=app_settings["rebuilt_wrapper_prompt"],
        )
        _enable(session, sample_portfolio)
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        claimed = evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="codex-cli 0.144.5",
            limit=5,
            now=now,
        )
        assert [run["id"] for run in claimed["runs"]] == [run_id]
        execution_prompt = claimed["runs"][0]["execution_prompt"]
        assert sample_portfolio["slug"] in execution_prompt
        assert "Managed wrapper marker for" in execution_prompt
        assert "manage and rebalance the existing portfolio" in execution_prompt
        assert "Do not call any write tool" in execution_prompt
        assert "{{" not in execution_prompt

        submitted = evaluator.submit_run(
            session,
            run_id=run_id,
            positions=[
                {"symbol": "AAPL", "weight_pct": 55, "note": "consumer resilience"},
                {"symbol": "MSFT", "weight_pct": 45, "note": "cloud growth"},
            ],
            note="Immediate evaluation",
            report="Both theses remain intact.",
            now=now + timedelta(minutes=5),
        )

    assert submitted["run"]["status"] == "succeeded"
    assert submitted["run"]["trigger_kind"] == "manual"
    assert (
        submitted["allocation"]["effective_date"]
        == effective_date_for(now + timedelta(minutes=5)).isoformat()
    )


def test_scheduled_run_is_created_only_during_configured_window(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    scheduled_for = date(2026, 7, 20)
    inside_window = close_at(scheduled_for) - timedelta(minutes=60)
    with session_factory()() as session:
        _enable(session, sample_portfolio, [scheduled_for.weekday()])
        claimed = evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="codex-cli 0.144.5",
            limit=5,
            now=inside_window,
        )

    assert len(claimed["runs"]) == 1
    assert claimed["runs"][0]["trigger_kind"] == "scheduled"
    assert claimed["runs"][0]["scheduled_for"] == scheduled_for.isoformat()


def test_pause_keeps_queued_work_and_stops_claiming(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable(session, sample_portfolio)
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        evaluator.update_settings(session, enabled=False)
        claimed = evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="codex-cli 0.144.5",
            limit=5,
            now=now,
        )
        history = evaluator.list_runs(session)

    assert claimed["runs"] == []
    assert history["items"][0]["id"] == run_id
    assert history["items"][0]["status"] == "queued"


def test_disabling_portfolio_automation_cancels_queued_work(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable(session, sample_portfolio)
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=False,
            weekdays=[],
        )
        history = evaluator.list_runs(session)

    assert history["items"][0]["id"] == run_id
    assert history["items"][0]["status"] == "cancelled"


def test_running_cancel_request_finishes_as_cancelled(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable(session, sample_portfolio)
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
        requested = evaluator.cancel_run(session, run_id=run_id, now=now)
        cancelled = evaluator.fail_run(
            session,
            run_id=run_id,
            error="Cancelled by an administrator.",
            cancelled=True,
            now=now,
        )

    assert requested["status"] == "cancel_requested"
    assert cancelled["status"] == "cancelled"


def test_failed_run_retry_creates_linked_manual_run(sample_portfolio):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _enable(session, sample_portfolio)
        evaluator.update_settings(session, max_attempts=1)
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
        failed = evaluator.fail_run(session, run_id=run_id, error="research failed", now=now)
        retried = evaluator.retry_run(session, run_id=run_id, now=now)

    assert failed["status"] == "failed"
    assert retried["run"]["trigger_kind"] == "retry"
    assert retried["run"]["retry_of_run_id"] == run_id


def test_admin_dashboard_and_internal_worker_auth(
    client,
    admin_headers,
    sample_portfolio,
):
    assert client.get("/api/evaluator").status_code == 401
    dashboard = client.get("/api/evaluator", headers=admin_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["settings"]["max_concurrency"] == 5

    denied = client.post(
        "/api/internal/evaluator/claim",
        json={
            "worker_id": "worker-1",
            "harness": "codex",
            "harness_version": "test",
            "limit": 1,
        },
    )
    assert denied.status_code == 401
    allowed = client.post(
        "/api/internal/evaluator/heartbeat",
        headers={"Authorization": "Bearer test-internal-worker-token"},
        json={
            "instance_id": "worker-1",
            "harness": "codex",
            "status": "idle",
            "harness_version": "codex-cli 0.144.5",
            "authenticated": True,
            "active_run_count": 0,
            "last_error": None,
        },
    )
    assert allowed.status_code == 200
