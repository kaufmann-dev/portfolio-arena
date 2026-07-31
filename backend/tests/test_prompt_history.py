"""Prompt identity, immutable version history, and archive lifecycle tests."""

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import EvaluationRun, Portfolio, Prompt, PromptVersion


def _create_prompt(client, admin_headers, *, name: str = "Alpha Strategy") -> dict:
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": name,
            "text": "Choose evidence-backed opportunities.",
            "notes": "initial note",
            "allocation_policy": {
                "min_position_weight_pct": 10.1,
                "max_position_weight_pct": 25.5,
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_portfolio(client, admin_headers, sample_agent, prompt_id: int) -> dict:
    response = client.post(
        "/api/portfolios",
        json={
            "name": "Prompt History Portfolio",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt_id,
            "prompt_mode": "managed",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_commits_v1_and_populates_current_pointer(client, admin_headers):
    created = _create_prompt(client, admin_headers)

    assert created["status"] == "active"
    assert created["archived_at"] is None
    assert created["current_version"] == 1
    assert created["version_count"] == 1
    assert created["portfolio_count"] == 0
    assert created["name"] == "Alpha Strategy"
    assert created["text"] == "Choose evidence-backed opportunities."
    assert created["notes"] == "initial note"
    assert created["allocation_policy"]["min_position_weight_pct"] == 10.1
    assert created["allocation_policy"]["max_position_weight_pct"] == 25.5

    with session_factory()() as session:
        prompt = session.get(Prompt, created["id"])
        assert prompt is not None
        assert prompt.current_version_id is not None
        assert prompt.current_version is not None
        assert prompt.current_version.version == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PromptVersion)
                .where(PromptVersion.prompt_id == created["id"])
            )
            == 1
        )


def test_update_appends_immutable_version_and_noop_does_not(client, admin_headers):
    created = _create_prompt(client, admin_headers)

    noop = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={
            "name": "Alpha Strategy",
            "allocation_policy": {
                "min_position_weight_pct": 10.1,
                "max_position_weight_pct": 25.5,
            },
        },
        headers=admin_headers,
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["current_version"] == 1
    assert noop.json()["version_count"] == 1
    assert noop.json()["updated_at"] == created["updated_at"]

    updated = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={
            "name": "Alpha Strategy v2",
            "text": "Choose opportunities with a specific catalyst.",
            "notes": "second note",
            "allocation_policy": {
                "min_position_weight_pct": 20,
                "max_position_weight_pct": 40,
            },
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"] == 2
    assert updated.json()["version_count"] == 2

    history = client.get(
        f"/api/admin/prompts/{created['id']}/versions",
        headers=admin_headers,
    )
    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["prompt_id"] == created["id"]
    assert [version["version"] for version in payload["versions"]] == [2, 1]
    assert payload["versions"][0]["name"] == "Alpha Strategy v2"
    assert payload["versions"][0]["text"] == "Choose opportunities with a specific catalyst."
    assert payload["versions"][0]["restored_from_version"] is None
    assert payload["versions"][1]["name"] == "Alpha Strategy"
    assert payload["versions"][1]["text"] == "Choose evidence-backed opportunities."
    assert set(payload["versions"][0]) == {
        "version",
        "name",
        "text",
        "notes",
        "allocation_policy",
        "created_at",
        "restored_from_version",
    }


def test_admin_list_has_status_history_and_usage_metadata(
    client,
    admin_headers,
    sample_agent,
):
    created = _create_prompt(client, admin_headers)
    _create_portfolio(client, admin_headers, sample_agent, created["id"])

    response = client.get("/api/admin/prompts?status=all", headers=admin_headers)
    assert response.status_code == 200, response.text
    row = next(prompt for prompt in response.json()["prompts"] if prompt["id"] == created["id"])
    assert set(row) == {
        "id",
        "slug",
        "status",
        "archived_at",
        "created_at",
        "updated_at",
        "current_version",
        "version_count",
        "portfolio_count",
        "name",
        "text",
        "notes",
        "allocation_policy",
    }
    assert row["current_version"] == 1
    assert row["version_count"] == 1
    assert row["portfolio_count"] == 1


def test_archive_is_blocked_while_any_portfolio_references_prompt(
    client,
    admin_headers,
    sample_agent,
):
    created = _create_prompt(client, admin_headers)
    _create_portfolio(client, admin_headers, sample_agent, created["id"])

    response = client.post(
        f"/api/admin/prompts/{created['id']}/archive",
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert "existing portfolio" in response.json()["detail"]


def test_restore_appends_version_and_preserves_archive_status(client, admin_headers):
    created = _create_prompt(client, admin_headers)
    archived = client.post(
        f"/api/admin/prompts/{created['id']}/archive",
        headers=admin_headers,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None

    blocked_update = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"text": "This edit must not be accepted."},
        headers=admin_headers,
    )
    assert blocked_update.status_code == 409

    restored = client.post(
        f"/api/admin/prompts/{created['id']}/versions/1/restore",
        headers=admin_headers,
    )
    assert restored.status_code == 201, restored.text
    assert restored.json()["status"] == "archived"
    assert restored.json()["archived_at"] == archived.json()["archived_at"]
    assert restored.json()["current_version"] == 2
    assert restored.json()["version_count"] == 2

    history = client.get(
        f"/api/admin/prompts/{created['id']}/versions",
        headers=admin_headers,
    ).json()
    assert history["versions"][0]["version"] == 2
    assert history["versions"][0]["restored_from_version"] == 1

    unarchived = client.post(
        f"/api/admin/prompts/{created['id']}/unarchive",
        headers=admin_headers,
    )
    assert unarchived.status_code == 200, unarchived.text
    assert unarchived.json()["status"] == "active"
    assert unarchived.json()["archived_at"] is None


@pytest.mark.parametrize("run_status", ["running", "cancel_requested"])
def test_running_evaluation_blocks_update_and_restore(
    client,
    admin_headers,
    sample_agent,
    run_status,
):
    created = _create_prompt(client, admin_headers)
    portfolio_data = _create_portfolio(client, admin_headers, sample_agent, created["id"])
    with session_factory()() as session:
        portfolio = session.get(Portfolio, portfolio_data["id"])
        assert portfolio is not None
        session.add(
            EvaluationRun(
                portfolio_id=portfolio.id,
                agent_id=portfolio.agent_id,
                model_id=sample_agent["model"]["id"],
                scheduled_for=None,
                trigger_kind="manual",
                harness="codex",
                execution_model_id="gpt-5.6-sol",
                reasoning_effort="xhigh",
                status=run_status,
            )
        )
        session.commit()

    update = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"text": "A change while the evaluator is active."},
        headers=admin_headers,
    )
    restore = client.post(
        f"/api/admin/prompts/{created['id']}/versions/1/restore",
        headers=admin_headers,
    )

    assert update.status_code == 409
    assert restore.status_code == 409


def test_unknown_prompt_and_version_return_not_found(client, admin_headers):
    created = _create_prompt(client, admin_headers)

    missing_prompt = client.get(
        "/api/admin/prompts/999999/versions",
        headers=admin_headers,
    )
    missing_version = client.post(
        f"/api/admin/prompts/{created['id']}/versions/999/restore",
        headers=admin_headers,
    )

    assert missing_prompt.status_code == 404
    assert missing_version.status_code == 404
