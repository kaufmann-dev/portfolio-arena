"""Admin inventory and destructive actions against real database references."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import Allocation, EvaluationRun, Portfolio, PortfolioEvaluatorConfig
from app.services import evaluator
from app.services.errors import AdminOpError

from .test_meta_admin import _create_arena_prompt


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


@pytest.mark.parametrize("status,expected", [("queued", "cancelled"), ("running", "cancel_requested")])
def test_archive_stops_evaluations_immediately(client, admin_headers, sample_portfolio, status, expected):
    run_id = add_run(sample_portfolio, status)
    response = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}", json={"status": "archived"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    with session_factory()() as session:
        assert session.get(EvaluationRun, run_id).status == expected
        assert session.get(PortfolioEvaluatorConfig, sample_portfolio["id"]).enabled is False


def test_archived_dependencies_allow_rename_but_block_restore(
    client, admin_headers, sample_portfolio, sample_prompt
):
    url = f"/api/portfolios/{sample_portfolio['id']}"
    assert client.patch(url, json={"status": "archived"}, headers=admin_headers).status_code == 200
    assert (
        client.post(f"/api/admin/prompts/{sample_prompt['id']}/archive", headers=admin_headers).status_code
        == 200
    )
    renamed = client.patch(
        url,
        json={"name": "Renamed", "prompt_id": sample_prompt["id"], "prompt_mode": "managed"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200, renamed.text
    restored = client.patch(url, json={"status": "active"}, headers=admin_headers)
    assert restored.status_code == 409
    assert "prompt" in restored.json()["detail"].lower()


def test_inventory_includes_meta_archives_and_usage_without_valuation(
    client, admin_headers, sample_portfolio, sample_agent, monkeypatch
):
    prompt = _create_arena_prompt(client, admin_headers)
    family = client.post(
        "/api/admin/meta-portfolio-sets",
        json={"family_name": "Confluence", "agent_id": sample_agent["id"], "prompt_id": prompt["id"]},
        headers=admin_headers,
    ).json()
    member = family["portfolios"][0]
    client.patch(f"/api/portfolios/{member['id']}", json={"status": "archived"}, headers=admin_headers)
    add_run(sample_portfolio, "running")

    def no_valuation(*args, **kwargs):
        raise AssertionError("Admin inventory must not calculate performance")

    monkeypatch.setattr("app.services.admin_ops.compute_valuations", no_valuation)
    monkeypatch.setattr("app.services.admin_ops.compute_rebuilt_arena", no_valuation)
    response = client.get("/api/admin/portfolios", headers=admin_headers)
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["portfolios"]}
    assert len(rows) == 5
    assert rows[member["id"]]["status"] == "archived"
    assert rows[member["id"]]["meta_set_id"] == family["id"]
    assert rows[sample_portfolio["id"]]["evaluation_run_count"] == 1
    assert rows[sample_portfolio["id"]]["structure_editable"] is False
    assert rows[sample_portfolio["id"]]["allocation_count"] == 1
    assert client.get("/api/admin/portfolios").status_code == 401


def test_blank_portfolio_name_rejected(client, admin_headers, sample_portfolio):
    response = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}", json={"name": "   "}, headers=admin_headers
    )
    assert response.status_code == 422


def test_meta_deletion_removes_runs_and_keeps_siblings(client, admin_headers, sample_agent):
    from app.models import MetaPortfolioSet

    prompt = _create_arena_prompt(client, admin_headers)
    family = client.post(
        "/api/admin/meta-portfolio-sets",
        json={"family_name": "With runs", "agent_id": sample_agent["id"], "prompt_id": prompt["id"]},
        headers=admin_headers,
    ).json()
    member, sibling = family["portfolios"][:2]
    deleted_run = add_run(member, "running")
    kept_run = add_run(sibling, "succeeded")
    assert client.delete(f"/api/portfolios/{member['id']}", headers=admin_headers).status_code == 200
    with session_factory()() as session:
        assert session.get(EvaluationRun, deleted_run) is None
        assert session.get(EvaluationRun, kept_run) is not None
        assert session.get(MetaPortfolioSet, family["id"]) is not None
        assert len(session.get(MetaPortfolioSet, family["id"]).portfolios) == 3


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
