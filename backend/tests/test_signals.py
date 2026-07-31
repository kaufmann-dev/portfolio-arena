"""Daily rebuilt signal entry, immutability, reset, and evaluator routing."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.services import admin_ops, evaluator
from app.services.admin_ops import AdminOpError
from app.services.trading_calendar import close_at, effective_date_for


def _create_rebuilt(client, admin_headers, sample_agent, sample_prompt) -> dict:
    response = client.post(
        "/api/portfolios",
        headers=admin_headers,
        json={
            "name": "Independent Daily Signals",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "rebuilt",
            "direction": "long",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _signal_body(symbol: str = "AAPL") -> dict:
    return {
        "positions": [{"symbol": symbol, "weight_pct": 100, "note": "current thesis"}],
        "note": "independent daily signal",
    }


def test_browser_signal_is_pending_editable_and_one_per_session(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)

    allocation = client.post(
        f"/api/portfolios/{portfolio['id']}/allocations",
        headers=admin_headers,
        json=_signal_body(),
    )
    assert allocation.status_code == 409

    created = client.post(
        f"/api/portfolios/{portfolio['id']}/signals",
        headers=admin_headers,
        json=_signal_body(),
    )
    assert created.status_code == 201, created.text
    signal = created.json()
    assert signal["portfolio_id"] == portfolio["id"]
    assert signal["provenance"] == "browser_admin"
    assert signal["locked"] is False
    assert signal["positions"][0]["symbol"] == "AAPL"

    duplicate = client.post(
        f"/api/portfolios/{portfolio['id']}/signals",
        headers=admin_headers,
        json=_signal_body("MSFT"),
    )
    assert duplicate.status_code == 409

    updated = client.put(
        f"/api/signals/{signal['id']}",
        headers=admin_headers,
        json=_signal_body("MSFT"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["positions"][0]["symbol"] == "MSFT"
    assert client.delete(f"/api/signals/{signal['id']}", headers=admin_headers).status_code == 200


def test_managed_portfolio_rejects_signals(client, admin_headers, sample_portfolio):
    response = client.post(
        f"/api/portfolios/{sample_portfolio['id']}/signals",
        headers=admin_headers,
        json=_signal_body(),
    )
    assert response.status_code == 409


def test_effective_signal_is_completely_immutable(
    sample_agent,
    sample_prompt,
    client,
    admin_headers,
):
    from app.db import session_factory

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    entered_at = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        created = admin_ops.create_signal(
            session,
            portfolio["id"],
            _signal_body()["positions"],
            "mcp-created",
            now=entered_at,
        )
        locked_at = close_at(date.fromisoformat(created["effective_date"]))
        with pytest.raises(AdminOpError, match="immutable"):
            admin_ops.update_signal(
                session,
                created["id"],
                note="must not change",
                now=locked_at,
            )
        with pytest.raises(AdminOpError, match="immutable"):
            admin_ops.delete_signal(session, created["id"], now=locked_at)


def test_signal_reset_enables_mode_change_and_preserves_mode_separation(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    from app.db import session_factory
    from app.models import Portfolio

    with session_factory()() as session:
        row = session.get(Portfolio, portfolio["id"])
        row.founding_v2 = True
        session.commit()
    signal = client.post(
        f"/api/portfolios/{portfolio['id']}/signals",
        headers=admin_headers,
        json=_signal_body(),
    )
    assert signal.status_code == 201, signal.text

    blocked = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert blocked.status_code == 409

    reset = client.post(
        f"/api/portfolios/{portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["deleted_allocations"] == 0
    assert reset.json()["deleted_signals"] == 1
    with session_factory()() as session:
        assert session.get(Portfolio, portfolio["id"]).founding_v2 is False

    changed = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert changed.status_code == 200, changed.text


def test_empty_founding_portfolio_requires_reset_before_mode_change(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    from app.db import session_factory
    from app.models import Portfolio

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    with session_factory()() as session:
        row = session.get(Portfolio, portfolio["id"])
        row.founding_v2 = True
        session.commit()

    blocked = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert blocked.status_code == 409

    reset = client.post(
        f"/api/portfolios/{portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["deleted_signals"] == 0

    changed = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert changed.status_code == 200, changed.text
    with session_factory()() as session:
        row = session.get(Portfolio, portfolio["id"])
        assert row.prompt_mode == "managed"
        assert row.founding_v2 is False


@pytest.mark.parametrize("claim_run", [False, True], ids=["queued", "running"])
def test_evaluation_work_requires_reset_before_mode_change(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    claim_run,
):
    from app.db import session_factory
    from app.models import EvaluationRun

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        if claim_run:
            evaluator.claim_runs(
                session,
                worker_id="worker-1",
                harness="codex",
                harness_version="test",
                limit=1,
                now=now,
            )

    blocked = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert blocked.status_code == 409

    reset = client.post(
        f"/api/portfolios/{portfolio['id']}/reset",
        headers=admin_headers,
    )
    assert reset.status_code == 200, reset.text
    if claim_run:
        assert reset.json()["cancellation_requested_runs"] == 1
        still_blocked = client.patch(
            f"/api/portfolios/{portfolio['id']}",
            headers=admin_headers,
            json={"prompt_mode": "managed"},
        )
        assert still_blocked.status_code == 409
        with session_factory()() as session:
            evaluator.fail_run(
                session,
                run_id=run_id,
                error="Cancelled after the portfolio reset.",
                cancelled=True,
                now=now,
            )
        expected_status = "cancelled"
    else:
        assert reset.json()["cancelled_queued_runs"] == 1
        expected_status = "cancelled"

    changed = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"prompt_mode": "managed"},
    )
    assert changed.status_code == 200, changed.text
    with session_factory()() as session:
        assert session.get(EvaluationRun, run_id).status == expected_status


def test_rebuilt_evaluator_forces_daily_schedule_and_routes_manual_result_to_signal(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    from app.db import session_factory
    from app.models import Signal

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        config = evaluator.update_portfolio_config(
            session,
            portfolio_id=portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        assert config["weekdays"] == [0, 1, 2, 3, 4]
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[portfolio["id"]],
            now=now,
        )
        run_id = queued["items"][0]["run"]["id"]
        evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="test",
            limit=1,
            now=now,
        )
        submitted = evaluator.submit_run(
            session,
            run_id=run_id,
            positions=_signal_body()["positions"],
            note="manual integrated signal",
            report="complete",
            now=now + timedelta(minutes=5),
        )
        signal = session.get(Signal, submitted["result"]["id"])

    assert submitted["run"]["result"]["kind"] == "signal"
    assert submitted["result"]["kind"] == "signal"
    assert signal.effective_date == effective_date_for(now + timedelta(minutes=5))
    assert signal.provenance == "integrated"


def test_scheduled_rebuilt_submission_keeps_scheduled_close_after_market(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    from app.db import session_factory
    from app.models import Signal

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    scheduled_for = date(2026, 7, 20)
    before_close = close_at(scheduled_for) - timedelta(minutes=60)
    after_close = close_at(scheduled_for) + timedelta(minutes=1)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="test",
            limit=0,
            now=before_close,
        )
        claimed = evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="test",
            limit=1,
            now=after_close,
        )
        submitted = evaluator.submit_run(
            session,
            run_id=claimed["runs"][0]["id"],
            positions=_signal_body()["positions"],
            note="late scheduled signal",
            report="scheduled session remains authoritative",
            now=after_close + timedelta(minutes=1),
        )
        signal = session.get(Signal, submitted["result"]["id"])

    assert signal.effective_date == scheduled_for


def test_duplicate_evaluator_submission_is_skipped_without_claiming_existing_signal(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    from app.db import session_factory
    from app.models import Portfolio, Signal

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    scheduled_for = date(2026, 7, 20)
    before_close = close_at(scheduled_for) - timedelta(minutes=60)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        claimed = evaluator.claim_runs(
            session,
            worker_id="worker-1",
            harness="codex",
            harness_version="test",
            limit=1,
            now=before_close,
        )
        existing = admin_ops._new_signal(
            session.get(Portfolio, portfolio["id"]),
            _signal_body()["positions"],
            "existing signal",
            before_close,
            scheduled_for,
            "browser_admin",
        )
        session.add(existing)
        session.commit()

        submitted = evaluator.submit_run(
            session,
            run_id=claimed["runs"][0]["id"],
            positions=_signal_body("MSFT")["positions"],
            note="must not claim existing",
            report="duplicate",
            now=before_close + timedelta(minutes=1),
        )
        signals = session.scalars(select(Signal).where(Signal.portfolio_id == portfolio["id"])).all()

    assert submitted["run"]["status"] == "skipped"
    assert submitted["run"]["result"] is None
    assert submitted["result"] is None
    assert len(signals) == 1
