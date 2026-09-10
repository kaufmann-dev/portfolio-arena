"""Deleting research removes its decision and run without touching other history."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import Allocation, EvaluationRun, Portfolio, Position, Signal, SignalPosition
from app.services import admin_ops, evaluator
from app.services.errors import AdminOpError
from tests.test_admin_lifecycle import add_run
from tests.test_mcp import _call_tool


def _history(client, headers, agent, prompt, mode):
    response = client.post(
        "/api/portfolios",
        json={
            "name": f"Delete {mode}",
            "version_id": 1,
            "agent_id": agent["id"],
            "prompt_id": prompt["id"],
            "prompt_mode": mode,
            "direction": "long",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    portfolio = response.json()
    results = []
    with session_factory()() as session:
        record = session.get(Portfolio, portfolio["id"])
        for day in (date(2026, 7, 20), date(2026, 7, 21)):
            positions = [{"symbol": "AAPL", "weight_pct": 100, "note": "thesis"}]
            entered = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            decision = (
                admin_ops._new_allocation(record, positions, "decision", entered, day)
                if mode == "managed"
                else admin_ops._new_signal(record, positions, "decision", entered, day, "integrated")
            )
            session.add(decision)
            session.flush()
            run = EvaluationRun(
                portfolio_id=record.id,
                agent_id=record.agent_id,
                model_id=record.agent.model_id,
                harness="codex",
                execution_model_id="test-model",
                status="succeeded",
                trigger_kind="manual",
                report="Research report",
                allocation_id=decision.id if mode == "managed" else None,
                signal_id=decision.id if mode == "rebuilt" else None,
            )
            session.add(run)
            session.flush()
            results.append((run.id, decision.id))
        session.commit()
    return portfolio, results


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("transport", ["run_api", "decision_api", "run_mcp", "decision_mcp"])
def test_delete_one_evaluation_and_result_preserves_other_history(
    client,
    admin_headers,
    mcp_headers,
    sample_agent,
    sample_prompt,
    mode,
    transport,
):
    from app.services.market_refresh import refresh_market_data_once

    portfolio, ((run_id, decision_id), (remaining_run, remaining_decision)) = _history(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        mode,
    )
    kind = "allocation" if mode == "managed" else "signal"
    model = Allocation if mode == "managed" else Signal
    position_model = Position if mode == "managed" else SignalPosition
    position_fk = Position.allocation_id if mode == "managed" else SignalPosition.signal_id
    refresh_market_data_once()
    url = f"/api/portfolios/{portfolio['slug']}"
    before = client.get(url).json()["portfolio"]
    if transport.endswith("api"):
        path = f"/api/evaluator/runs/{run_id}" if transport == "run_api" else f"/api/{kind}s/{decision_id}"
        assert client.delete(path).status_code == 401
        deleted = client.delete(path, headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        assert client.delete(path, headers=admin_headers).status_code == 404
    else:
        tool = "delete_evaluation_run" if transport == "run_mcp" else f"delete_{kind}"
        arguments = {"run_id": run_id} if transport == "run_mcp" else {f"{kind}_id": decision_id}
        assert _call_tool(client, mcp_headers, tool, arguments) == {"ok": True}

    with session_factory()() as session:
        assert session.get(EvaluationRun, run_id) is None
        assert session.get(model, decision_id) is None
        assert (
            session.scalar(select(func.count()).select_from(position_model).where(position_fk == decision_id))
            == 0
        )
        assert session.get(EvaluationRun, remaining_run).report == "Research report"
        assert session.get(model, remaining_decision) is not None
    runs = client.get(f"/api/evaluation-runs?portfolio_id={portfolio['id']}", headers=admin_headers).json()
    assert [run["id"] for run in runs["items"]] == [remaining_run]
    agents = _call_tool(client, mcp_headers, "list_agents")["agents"]
    assert next(agent for agent in agents if agent["id"] == sample_agent["id"])["evaluation_run_count"] == 1
    after = client.get(url).json()["portfolio"]
    if mode == "managed":
        assert len(before["allocations"]) == 2 and len(after["allocations"]) == 1
        assert before["inception"] != after["inception"]
    else:
        assert len(before["signals"]) == 2 and len(after["signals"]) == 1
        assert after["selected_policy"] is None  # One signal is insufficient evidence.
    assert before["series"] != after["series"]


@pytest.mark.parametrize(
    "status", ["queued", "running", "cancel_requested", "failed", "cancelled", "skipped"]
)
def test_delete_run_without_result_blocks_late_worker_submission(
    client, admin_headers, sample_portfolio, status
):
    run_id = add_run(sample_portfolio, status)
    response = client.delete(f"/api/evaluator/runs/{run_id}", headers=admin_headers)
    assert response.status_code == 200, response.text
    with session_factory()() as session:
        assert session.get(EvaluationRun, run_id) is None
        assert session.get(Allocation, sample_portfolio["allocation"]["id"]) is not None
        with pytest.raises(AdminOpError, match="not found"):
            evaluator.submit_run(session, run_id=run_id, positions=[], note="late", report="late")


def test_delete_failed_parent_keeps_later_retry(client, admin_headers, sample_portfolio):
    parent = add_run(sample_portfolio, "failed")
    child = add_run(sample_portfolio, "succeeded")
    with session_factory()() as session:
        session.get(EvaluationRun, child).retry_of_run_id = parent
        session.commit()
    response = client.delete(f"/api/evaluator/runs/{parent}", headers=admin_headers)
    assert response.status_code == 200, response.text
    with session_factory()() as session:
        assert session.get(EvaluationRun, child).retry_of_run_id is None


@pytest.mark.parametrize("operation", ["reset", "delete_run"])
def test_history_deletion_waits_for_submission_and_removes_its_result(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    monkeypatch,
    operation,
):
    portfolio, _ = _history(client, admin_headers, sample_agent, sample_prompt, "managed")
    run_id = add_run(portfolio, "running")
    submitting = Event()
    deleting = Event()
    release = Event()
    normalize = admin_ops._normalize_positions
    lock = admin_ops._lock_portfolio_lifecycle

    def paused_normalize(*args):
        submitting.set()  # submit_run already holds its run and portfolio locks.
        assert release.wait(5)
        return normalize(*args)

    def observed_lock(*args):
        deleting.set()
        return lock(*args)

    monkeypatch.setattr(admin_ops, "_normalize_positions", paused_normalize)
    monkeypatch.setattr(admin_ops, "_lock_portfolio_lifecycle", observed_lock)

    def submit():
        with session_factory()() as session:
            return evaluator.submit_run(
                session,
                run_id=run_id,
                positions=[{"symbol": "MSFT", "weight_pct": 100}],
                note="late",
                report="late",
            )

    def remove():
        with session_factory()() as session:
            if operation == "reset":
                return admin_ops.reset_portfolio(session, portfolio["id"])
            return evaluator.delete_run(session, run_id=run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = pool.submit(submit)
        try:
            assert submitting.wait(5)
            removed = pool.submit(remove)
            assert deleting.wait(5)
        finally:
            release.set()
        result = submitted.result(timeout=5)
        assert removed.result(timeout=5)["ok"] is True
    with session_factory()() as session:
        assert session.get(EvaluationRun, run_id) is None
        assert session.get(Allocation, result["result"]["id"]) is None
        assert session.scalar(
            select(func.count()).select_from(Allocation).where(Allocation.portfolio_id == portfolio["id"])
        ) == (0 if operation == "reset" else 2)


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
def test_reset_removes_run_references_and_allows_deleting_former_agent(
    client,
    admin_headers,
    mcp_headers,
    sample_agent,
    sample_model,
    sample_prompt,
    mode,
):
    portfolio, _ = _history(client, admin_headers, sample_agent, sample_prompt, mode)
    replacement = client.post(
        "/api/agents",
        headers=admin_headers,
        json={"model_id": sample_model["id"], "harness": "codex", "reasoning_effort": "high"},
    )
    assert replacement.status_code == 201, replacement.text
    changed = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        headers=admin_headers,
        json={"agent_id": replacement.json()["id"]},
    )
    assert changed.status_code == 200, changed.text
    assert client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers).status_code == 409
    other = client.post(
        "/api/portfolios",
        headers=admin_headers,
        json={
            "name": "Unrelated",
            "version_id": 1,
            "agent_id": replacement.json()["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
        },
    ).json()
    other_run = add_run(other, "succeeded")
    reset = _call_tool(client, mcp_headers, "reset_portfolio", {"portfolio_id": portfolio["id"]})
    assert reset["deleted_evaluation_runs"] == 2
    assert reset["deleted_allocations"] == (2 if mode == "managed" else 0)
    assert reset["deleted_signals"] == (2 if mode == "rebuilt" else 0)
    inventory = _call_tool(client, mcp_headers, "list_agents")
    old_agent = next(agent for agent in inventory["agents"] if agent["id"] == sample_agent["id"])
    assert old_agent["evaluation_run_count"] == 0
    assert old_agent["can_delete"] is True
    assert client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers).status_code == 200
    with session_factory()() as session:
        assert session.get(EvaluationRun, other_run) is not None
        assert session.get(Portfolio, portfolio["id"]).agent_id == replacement.json()["id"]
