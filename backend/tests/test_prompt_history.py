"""Prompt identity, immutable version history, and archive lifecycle tests."""

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import EvaluationRun, Portfolio, Prompt, PromptVersion


def _create_prompt(
    client,
    admin_headers,
    *,
    name: str = "Alpha Strategy",
    mode: str = "both",
    managed_text: str | None = "Choose evidence-backed managed opportunities.",
    rebuilt_text: str | None = "Choose fresh evidence-backed rebuilt opportunities.",
) -> dict:
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": name,
            "mode": mode,
            "managed_text": managed_text,
            "rebuilt_text": rebuilt_text,
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


def _create_portfolio(
    client,
    admin_headers,
    sample_agent,
    prompt_id: int,
    *,
    prompt_mode: str = "managed",
) -> dict:
    response = client.post(
        "/api/portfolios",
        json={
            "name": f"Prompt History {prompt_mode.title()} Portfolio",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt_id,
            "prompt_mode": prompt_mode,
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
    assert created["mode"] == "both"
    assert created["managed_text"] == "Choose evidence-backed managed opportunities."
    assert created["rebuilt_text"] == "Choose fresh evidence-backed rebuilt opportunities."
    assert "text" not in created
    assert created["notes"] == "initial note"
    assert created["allocation_policy"]["min_position_weight_pct"] == 10.1
    assert created["allocation_policy"]["max_position_weight_pct"] == 25.5

    with session_factory()() as session:
        prompt = session.get(Prompt, created["id"])
        assert prompt is not None
        assert prompt.current_version_id is not None
        assert prompt.current_version is not None
        assert prompt.current_version.version == 1
        assert prompt.current_version.mode == "both"
        assert prompt.current_version.managed_text == created["managed_text"]
        assert prompt.current_version.rebuilt_text == created["rebuilt_text"]
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
            "managed_text": "Choose managed opportunities with a specific catalyst.",
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
    assert payload["versions"][0]["mode"] == "both"
    assert payload["versions"][0]["managed_text"] == "Choose managed opportunities with a specific catalyst."
    assert payload["versions"][0]["rebuilt_text"] == created["rebuilt_text"]
    assert payload["versions"][0]["restored_from_version"] is None
    assert payload["versions"][1]["name"] == "Alpha Strategy"
    assert payload["versions"][1]["mode"] == "both"
    assert payload["versions"][1]["managed_text"] == created["managed_text"]
    assert payload["versions"][1]["rebuilt_text"] == created["rebuilt_text"]
    assert set(payload["versions"][0]) == {
        "version",
        "name",
        "mode",
        "managed_text",
        "rebuilt_text",
        "notes",
        "allocation_policy",
        "created_at",
        "restored_from_version",
    }


@pytest.mark.parametrize(
    ("mode", "managed_text", "rebuilt_text"),
    [
        ("managed", None, None),
        ("managed", "   ", None),
        ("managed", "Managed strategy.", "Unexpected rebuilt strategy."),
        ("rebuilt", None, None),
        ("rebuilt", None, "   "),
        ("rebuilt", "Unexpected managed strategy.", "Rebuilt strategy."),
        ("both", "Managed strategy.", None),
        ("both", None, "Rebuilt strategy."),
        ("unsupported", "Managed strategy.", "Rebuilt strategy."),
    ],
)
def test_create_rejects_invalid_mode_text_combinations(
    client,
    admin_headers,
    mode,
    managed_text,
    rebuilt_text,
):
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": "Invalid mode contract",
            "mode": mode,
            "managed_text": managed_text,
            "rebuilt_text": rebuilt_text,
            "allocation_policy": {
                "min_position_weight_pct": 10,
                "max_position_weight_pct": 25,
            },
        },
        headers=admin_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("mode", "managed_text", "rebuilt_text"),
    [
        ("managed", "Managed strategy.", None),
        ("rebuilt", None, "Rebuilt strategy."),
        ("both", "Managed strategy.", "Rebuilt strategy."),
    ],
)
def test_create_serializes_normalized_mode_texts(
    client,
    admin_headers,
    mode,
    managed_text,
    rebuilt_text,
):
    created = _create_prompt(
        client,
        admin_headers,
        name=f"Valid {mode} strategy",
        mode=mode,
        managed_text=managed_text,
        rebuilt_text=rebuilt_text,
    )

    assert created["mode"] == mode
    assert created["managed_text"] == managed_text
    assert created["rebuilt_text"] == rebuilt_text
    assert "text" not in created


def test_legacy_generic_text_field_is_rejected(client, admin_headers):
    create = client.post(
        "/api/admin/prompts",
        json={
            "name": "Legacy create",
            "mode": "managed",
            "managed_text": "Managed strategy.",
            "text": "Legacy generic strategy.",
            "allocation_policy": {
                "min_position_weight_pct": 10,
                "max_position_weight_pct": 25,
            },
        },
        headers=admin_headers,
    )
    assert create.status_code == 422

    created = _create_prompt(client, admin_headers)
    patch = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"text": "Legacy generic strategy."},
        headers=admin_headers,
    )
    assert patch.status_code == 422
    history = client.get(
        f"/api/admin/prompts/{created['id']}/versions",
        headers=admin_headers,
    ).json()
    assert [version["version"] for version in history["versions"]] == [1]


def test_patch_merges_then_normalizes_mode_specific_text(client, admin_headers):
    created = _create_prompt(client, admin_headers)

    narrowed = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"mode": "managed"},
        headers=admin_headers,
    )
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["mode"] == "managed"
    assert narrowed.json()["managed_text"] == created["managed_text"]
    assert narrowed.json()["rebuilt_text"] is None

    unsupported_text = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"rebuilt_text": "Not valid for a managed-only prompt."},
        headers=admin_headers,
    )
    assert unsupported_text.status_code == 422

    missing_expansion_text = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"mode": "both"},
        headers=admin_headers,
    )
    assert missing_expansion_text.status_code == 422

    expanded = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={
            "mode": "both",
            "rebuilt_text": "New rebuilt strategy.",
        },
        headers=admin_headers,
    )
    assert expanded.status_code == 200, expanded.text
    assert expanded.json()["mode"] == "both"
    assert expanded.json()["managed_text"] == created["managed_text"]
    assert expanded.json()["rebuilt_text"] == "New rebuilt strategy."


@pytest.mark.parametrize("portfolio_status", ["active", "archived"])
def test_narrowing_rejects_removing_mode_used_by_any_portfolio(
    client,
    admin_headers,
    sample_agent,
    portfolio_status,
):
    created = _create_prompt(client, admin_headers)
    portfolio = _create_portfolio(client, admin_headers, sample_agent, created["id"])
    if portfolio_status == "archived":
        archived = client.patch(
            f"/api/portfolios/{portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        assert archived.status_code == 200, archived.text

    response = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={"mode": "rebuilt"},
        headers=admin_headers,
    )

    assert response.status_code == 409


def test_restore_rejects_removing_mode_used_by_archived_portfolio(
    client,
    admin_headers,
    sample_agent,
):
    created = _create_prompt(
        client,
        admin_headers,
        mode="managed",
        rebuilt_text=None,
    )
    expanded = client.patch(
        f"/api/admin/prompts/{created['id']}",
        json={
            "mode": "both",
            "rebuilt_text": "Rebuilt strategy added in v2.",
        },
        headers=admin_headers,
    )
    assert expanded.status_code == 200, expanded.text
    portfolio = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        created["id"],
        prompt_mode="rebuilt",
    )
    archived = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        json={"status": "archived"},
        headers=admin_headers,
    )
    assert archived.status_code == 200, archived.text

    restored = client.post(
        f"/api/admin/prompts/{created['id']}/versions/1/restore",
        headers=admin_headers,
    )

    assert restored.status_code == 409


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
        "mode",
        "managed_text",
        "rebuilt_text",
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
        json={"managed_text": "This edit must not be accepted."},
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
    assert history["versions"][0]["mode"] == "both"
    assert history["versions"][0]["managed_text"] == created["managed_text"]
    assert history["versions"][0]["rebuilt_text"] == created["rebuilt_text"]
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
        json={"managed_text": "A change while the evaluator is active."},
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
