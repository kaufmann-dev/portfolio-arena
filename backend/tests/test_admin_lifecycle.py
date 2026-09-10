"""Admin inventory and destructive actions against real database references."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import Allocation, EvaluationRun, Portfolio, PortfolioEvaluatorConfig
from app.services import evaluator
from app.services.errors import AdminOpError


def add_run(portfolio, status):
    with session_factory()() as session:
        row = session.get(Portfolio, portfolio["id"])
        run = EvaluationRun(
            portfolio_id=row.id,
            agent_id=row.agent_id,
            model_id=row.agent.model_id,
            harness="codex",
            execution_model_id="test-model",
            reasoning_effort="high",
            status=status,
            trigger_kind="manual",
            lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(run)
        session.merge(PortfolioEvaluatorConfig(portfolio_id=row.id, enabled=True, weekdays=[0]))
        session.commit()
        return run.id


@pytest.mark.parametrize(
    "status", ["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled", "skipped"]
)
def test_delete_portfolio_with_runs(client, admin_headers, sample_portfolio, status):
    run_id = add_run(sample_portfolio, status)
    response = client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    with session_factory()() as session:
        assert session.get(Portfolio, sample_portfolio["id"]) is None
        assert session.get(EvaluationRun, run_id) is None
        assert session.get(PortfolioEvaluatorConfig, sample_portfolio["id"]) is None
        assert session.scalar(select(func.count()).select_from(Allocation)) == 0
        with pytest.raises(AdminOpError, match="not found"):
            evaluator.submit_run(session, run_id=run_id, positions=[], note="late", report="late")


def test_blank_portfolio_name_rejected(client, admin_headers, sample_portfolio):
    response = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}", json={"name": "   "}, headers=admin_headers
    )
    assert response.status_code == 422


def test_model_delete_blocker_includes_history_after_agent_moves(
    client, admin_headers, sample_portfolio, sample_agent, sample_model
):
    add_run(sample_portfolio, "succeeded")
    replacement = client.post(
        "/api/models", json={"name": "Replacement", "capabilities": []}, headers=admin_headers
    ).json()
    response = client.patch(
        f"/api/agents/{sample_agent['id']}",
        json={"model_id": replacement["id"], "harness": None, "reasoning_effort": None},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    model = next(
        row
        for row in client.get("/api/models", headers=admin_headers).json()["models"]
        if row["id"] == sample_model["id"]
    )
    assert model["agent_count"] == 0
    assert model["evaluation_run_count"] == 1
    assert model["can_delete"] is False
    assert "evaluation run" in model["delete_blocker"]
    assert client.delete(f"/api/models/{sample_model['id']}", headers=admin_headers).status_code == 409
    assert (
        client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers).status_code == 200
    )
    model = next(
        row
        for row in client.get("/api/models", headers=admin_headers).json()["models"]
        if row["id"] == sample_model["id"]
    )
    assert model["can_delete"] is True
    assert client.delete(f"/api/models/{sample_model['id']}", headers=admin_headers).status_code == 200
