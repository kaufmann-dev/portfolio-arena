"""Model catalog, Agent execution profiles, and automation eligibility."""

from datetime import UTC, datetime

from app.services import evaluator

from .util import backdate_allocation


def test_harness_registry_exposes_codex_reasoning_vocabulary(client, admin_headers):
    response = client.get("/api/harnesses", headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {
        "harnesses": [
            {
                "id": "codex",
                "name": "Codex",
                "automation_supported": True,
                "reasoning_efforts": [
                    {"id": "low", "name": "Low"},
                    {"id": "medium", "name": "Medium"},
                    {"id": "high", "name": "High"},
                    {"id": "xhigh", "name": "Extra high"},
                ],
            }
        ]
    }


def test_agent_profile_is_generated_and_must_match_model_capabilities(
    client,
    admin_headers,
    sample_model,
):
    missing_effort = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": None,
        },
        headers=admin_headers,
    )
    assert missing_effort.status_code == 422

    created = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": "high",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "GPT-5.6 Sol (Codex, High)"
    assert created.json()["execution_model_id"] == "gpt-5.6-sol"

    duplicate = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": "high",
        },
        headers=admin_headers,
    )
    assert duplicate.status_code == 409

    unsupported = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "opencode",
            "reasoning_effort": None,
        },
        headers=admin_headers,
    )
    assert unsupported.status_code == 422


def test_model_name_cannot_be_only_whitespace(client, admin_headers):
    response = client.post(
        "/api/models",
        json={"name": "   ", "capabilities": []},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Model name is required"


def test_model_can_explicitly_expose_no_reasoning_control(client, admin_headers):
    model = client.post(
        "/api/models",
        json={
            "name": "Fixed Reasoning Model",
            "capabilities": [
                {
                    "harness": "codex",
                    "execution_model_id": "fixed-reasoning-model",
                    "reasoning_efforts": [],
                }
            ],
        },
        headers=admin_headers,
    ).json()

    created = client.post(
        "/api/agents",
        json={
            "model_id": model["id"],
            "harness": "codex",
            "reasoning_effort": None,
        },
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Fixed Reasoning Model (Codex)"

    invalid = client.post(
        "/api/agents",
        json={
            "model_id": model["id"],
            "harness": "codex",
            "reasoning_effort": "high",
        },
        headers=admin_headers,
    )
    assert invalid.status_code == 422
    assert "do not expose reasoning effort" in invalid.json()["detail"]


def test_model_capabilities_can_change_in_place_but_not_invalidate_agents(
    client,
    admin_headers,
    sample_model,
    sample_agent,
):
    updated = client.patch(
        f"/api/models/{sample_model['id']}",
        json={
            "capabilities": [
                {
                    "harness": "codex",
                    "execution_model_id": "gpt-5.6-sol-2026-07",
                    "reasoning_efforts": ["high", "xhigh"],
                }
            ]
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["capabilities"][0]["execution_model_id"] == "gpt-5.6-sol-2026-07"

    invalid = client.patch(
        f"/api/models/{sample_model['id']}",
        json={
            "capabilities": [
                {
                    "harness": "codex",
                    "execution_model_id": "gpt-5.6-sol-2026-07",
                    "reasoning_efforts": ["high"],
                }
            ]
        },
        headers=admin_headers,
    )
    assert invalid.status_code == 409
    assert "xhigh" in invalid.json()["detail"]


def test_codex_portfolios_are_eligible_but_start_disabled(
    client,
    admin_headers,
    sample_portfolio,
    sample_model,
    sample_prompt,
):
    unsupported_agent = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": None,
            "reasoning_effort": None,
        },
        headers=admin_headers,
    ).json()
    unsupported_portfolio = client.post(
        "/api/portfolios",
        json={
            "name": "Manual only",
            "agent_id": unsupported_agent["id"],
            "prompt_id": sample_prompt["id"],
        },
        headers=admin_headers,
    )
    assert unsupported_portfolio.status_code == 201

    dashboard = client.get("/api/evaluator", headers=admin_headers).json()
    configs = {item["portfolio"]["id"]: item for item in dashboard["portfolios"]}

    assert configs[sample_portfolio["id"]]["enabled"] is False
    assert configs[sample_portfolio["id"]]["agent"]["harness"]["id"] == "codex"
    assert unsupported_portfolio.json()["id"] not in configs


def test_reassigning_to_manual_agent_disables_and_cancels_automation(
    client,
    admin_headers,
    sample_portfolio,
    sample_model,
):
    from app.db import session_factory
    from app.models import PortfolioEvaluatorConfig

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        run = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )["items"][0]["run"]

    manual_agent = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": None,
            "reasoning_effort": None,
        },
        headers=admin_headers,
    ).json()
    response = client.patch(
        f"/api/portfolios/{sample_portfolio['id']}",
        json={"agent_id": manual_agent["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200

    with session_factory()() as session:
        config = session.get(PortfolioEvaluatorConfig, sample_portfolio["id"])
        historical = evaluator.list_runs(session)["items"][0]

    assert config is not None
    assert config.enabled is False
    assert historical["id"] == run["id"]
    assert historical["status"] == "cancelled"
    assert "without integrated automation" in historical["error"]


def test_runs_snapshot_agent_profile_and_model_execution_id(
    client,
    admin_headers,
    sample_portfolio,
    sample_agent,
    sample_model,
):
    from app.db import session_factory

    backdate_allocation(sample_portfolio["allocation"]["id"])
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[],
        )
        first = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )["items"][0]["run"]
        evaluator.cancel_run(session, run_id=first["id"], now=now)

    agent_update = client.patch(
        f"/api/agents/{sample_agent['id']}",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": "high",
        },
        headers=admin_headers,
    )
    assert agent_update.status_code == 200
    model_update = client.patch(
        f"/api/models/{sample_model['id']}",
        json={
            "capabilities": [
                {
                    "harness": "codex",
                    "execution_model_id": "gpt-5.6-sol-next",
                    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                }
            ]
        },
        headers=admin_headers,
    )
    assert model_update.status_code == 200

    with session_factory()() as session:
        historical = evaluator.list_runs(session)["items"][0]
        second = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )["items"][0]["run"]

    assert historical["execution_model_id"] == "gpt-5.6-sol"
    assert historical["reasoning_effort"] == "xhigh"
    assert historical["agent"]["name"] == "GPT-5.6 Sol (Codex, Extra high)"
    assert second["execution_model_id"] == "gpt-5.6-sol-next"
    assert second["reasoning_effort"] == "high"
    assert second["agent"]["name"] == "GPT-5.6 Sol (Codex, High)"
