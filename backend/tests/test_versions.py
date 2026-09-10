"""Version administration, preserved visibility, and permanent execution timing."""

from datetime import UTC, datetime

from app.db import session_factory
from app.models import EvaluationRun, Portfolio

from .test_admin_lifecycle import add_run


def test_version_lifecycle_and_scoped_inventory(client, admin_headers, sample_portfolio):
    created = client.post("/api/admin/versions", json={"name": "v2"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["evaluation_enabled"] is False
    assert client.get("/api/versions").json()["versions"][0]["id"] == version["id"]
    assert client.get(f"/api/admin/portfolios?version_id={version['id']}", headers=admin_headers).json() == {
        "portfolios": []
    }
    moved = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}", json={"version_id": version["id"]}, headers=admin_headers
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"]["id"] == version["id"]
    assert client.delete(f"/api/admin/versions/{version['id']}", headers=admin_headers).status_code == 409
    assert client.get("/api/admin/portfolios?version_id=1", headers=admin_headers).json()["portfolios"] == []
    rows = client.get(f"/api/arena/managed?version_id={version['id']}&direction=long").json()["portfolios"]
    assert any(row["id"] == sample_portfolio["id"] for row in rows)
    assert client.get(f"/api/portfolios/{sample_portfolio['slug']}").status_code == 200
    assert client.delete("/api/admin/versions/1", headers=admin_headers).status_code == 200
    assert client.get("/api/admin/portfolios?version_id=99999", headers=admin_headers).status_code == 404


def test_pausing_version_preserves_schedules_and_running_attempts(client, admin_headers, sample_portfolio):
    run_id = add_run(sample_portfolio, "queued")
    paused = client.patch("/api/admin/versions/1", json={"evaluation_enabled": False}, headers=admin_headers)
    assert paused.status_code == 200
    with session_factory()() as session:
        portfolio = session.get(Portfolio, sample_portfolio["id"])
        assert portfolio.evaluator_config.enabled is True
        assert portfolio.evaluator_config.weekdays == [0]
        assert session.get(EvaluationRun, run_id).status == "cancelled"
    running_id = add_run(sample_portfolio, "running")
    client.patch("/api/admin/versions/1", json={"evaluation_enabled": False}, headers=admin_headers)
    with session_factory()() as session:
        assert session.get(EvaluationRun, running_id).status == "running"
    # Ordinary editing and manual research entries remain possible while a version is paused.
    renamed = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}",
        json={"name": "Retained experiment"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200


def test_execution_timing_locks_forever_after_first_decision(client, admin_headers, sample_portfolio):
    url = f"/api/portfolios/{sample_portfolio['id']}"
    assert client.patch(url, json={"execution_boundary": "open"}, headers=admin_headers).status_code == 409
    assert client.post(url + "/reset", headers=admin_headers).status_code == 200
    assert client.patch(url, json={"execution_boundary": "open"}, headers=admin_headers).status_code == 409
    inventory = client.get("/api/admin/portfolios", headers=admin_headers).json()["portfolios"][0]
    assert inventory["execution_locked"] is True
    assert inventory["timing_editable"] is False


def test_empty_portfolio_timing_and_opening_preview(client, admin_headers, sample_agent, sample_prompt):
    created = client.post(
        "/api/portfolios",
        json={
            "name": "Morning",
            "version_id": 1,
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
            "execution_boundary": "close",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    portfolio = created.json()
    response = client.patch(
        f"/api/portfolios/{portfolio['id']}", json={"execution_boundary": "open"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["execution_locked"] is False
    preview = client.get(f"/api/effective-date?portfolio_id={portfolio['id']}", headers=admin_headers).json()
    assert preview["effective_at"]["phase"] == "open"
    assert datetime.fromisoformat(preview["effective_at"]["timestamp"]) > datetime.now(UTC)
    add_run(portfolio, "running")
    assert (
        client.patch(
            f"/api/portfolios/{portfolio['id']}", json={"execution_boundary": "close"}, headers=admin_headers
        ).status_code
        == 409
    )


def test_prompt_deletion_respects_recorded_revision_references(
    client,
    admin_headers,
    sample_portfolio,
    sample_prompt,
):
    old_prompt_id = sample_prompt["id"]
    url = f"/api/admin/prompts/{old_prompt_id}"
    assert client.delete(url, headers=admin_headers).status_code == 409
    run_id = add_run(sample_portfolio, "succeeded")
    with session_factory()() as session:
        portfolio = session.get(Portfolio, sample_portfolio["id"])
        session.get(EvaluationRun, run_id).prompt_version_id = portfolio.prompt.current_version_id
        session.commit()
    replacement = client.post(
        "/api/admin/prompts",
        json={
            "name": "Replacement",
            "mode": "managed",
            "direction": "long",
            "managed_long_text": "New strategy",
        },
        headers=admin_headers,
    ).json()
    changed = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}",
        json={"prompt_id": replacement["id"]},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert client.delete(url, headers=admin_headers).status_code == 409
    old = next(
        p
        for p in client.get("/api/admin/prompts", headers=admin_headers).json()["prompts"]
        if p["id"] == old_prompt_id
    )
    assert old["portfolio_count"] == 0 and old["evaluation_run_count"] == 1 and not old["can_delete"]
    assert (
        client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers).status_code == 200
    )
    assert client.delete(url, headers=admin_headers).status_code == 200
    assert client.get(url + "/versions", headers=admin_headers).status_code == 404


def test_unused_prompt_deletion_and_removed_fields(client, admin_headers, sample_prompt, sample_portfolio):
    assert (
        client.patch(
            f"/api/portfolios/{sample_portfolio['id']}", json={"cost_bps": 10}, headers=admin_headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/portfolios/{sample_portfolio['id']}", json={"status": "archived"}, headers=admin_headers
        ).status_code
        == 422
    )
    assert client.post(
        f"/api/admin/prompts/{sample_prompt['id']}/archive", headers=admin_headers
    ).status_code in {404, 405}
