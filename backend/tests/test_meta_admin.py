"""Admin integrity for arena-scoped prompts and atomic meta portfolio sets."""

from sqlalchemy import func, select

from app.db import session_factory
from app.models import MetaPortfolioSet, Portfolio, PortfolioEvaluatorConfig, Prompt


def _create_arena_prompt(client, admin_headers, *, name: str = "Arena Synthesis") -> dict:
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": name,
            "context_scope": "arena",
            "mode": "both",
            "direction": "both",
            "managed_long_text": "Synthesize managed long evidence.",
            "managed_short_text": "Synthesize managed short evidence.",
            "rebuilt_long_text": "Synthesize rebuilt long evidence.",
            "rebuilt_short_text": "Synthesize rebuilt short evidence.",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_prompt_scope_defaults_to_portfolio_and_is_immutable(
    client,
    admin_headers,
    sample_prompt,
):
    assert sample_prompt["context_scope"] == "portfolio"

    arena = _create_arena_prompt(client, admin_headers)
    assert arena["context_scope"] == "arena"

    response = client.patch(
        f"/api/admin/prompts/{arena['id']}",
        json={"context_scope": "portfolio"},
        headers=admin_headers,
    )
    assert response.status_code == 422

    with session_factory()() as session:
        assert session.get(Prompt, arena["id"]).context_scope == "arena"


def test_generic_portfolio_creation_rejects_arena_prompt(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_arena_prompt(client, admin_headers)
    response = client.post(
        "/api/portfolios",
        json={
            "name": "Bypass Meta Set",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "meta portfolio set" in response.json()["detail"].lower()


def test_create_meta_portfolio_set_is_atomic_and_automated(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_arena_prompt(client, admin_headers)
    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()

    assert created["slug"] == "confluence"
    assert created["family_name"] == "Confluence"
    assert created["variant_label"] is None
    assert created["agent_id"] == sample_agent["id"]
    assert created["prompt_id"] == prompt["id"]
    assert [portfolio["name"] for portfolio in created["portfolios"]] == [
        "Confluence Core",
        "Confluence Pulse",
        "Confluence Shadow",
        "Confluence Probe",
    ]
    assert [(portfolio["prompt_mode"], portfolio["direction"]) for portfolio in created["portfolios"]] == [
        ("managed", "long"),
        ("rebuilt", "long"),
        ("managed", "short"),
        ("rebuilt", "short"),
    ]
    assert all(portfolio["cost_bps"] == 10 for portfolio in created["portfolios"])
    assert all(
        portfolio["evaluator"] == {"enabled": True, "weekdays": [0, 1, 2, 3, 4]}
        for portfolio in created["portfolios"]
    )

    with session_factory()() as session:
        meta_set = session.get(MetaPortfolioSet, created["id"])
        assert meta_set is not None
        assert len(meta_set.portfolios) == 4
        configs = session.scalars(
            select(PortfolioEvaluatorConfig).where(
                PortfolioEvaluatorConfig.portfolio_id.in_(
                    [portfolio["id"] for portfolio in created["portfolios"]]
                )
            )
        ).all()
        assert len(configs) == 4
        assert all(config.enabled and config.weekdays == [0, 1, 2, 3, 4] for config in configs)


def test_meta_set_variant_appends_member_names_and_keeps_shared_family(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_arena_prompt(client, admin_headers)
    base = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    )
    assert base.status_code == 201, base.text

    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "variant_label": "Ultra",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["slug"] == "confluence-ultra"
    assert created["family_name"] == "Confluence"
    assert created["variant_label"] == "Ultra"
    assert [portfolio["name"] for portfolio in created["portfolios"]] == [
        "Confluence Core Ultra",
        "Confluence Pulse Ultra",
        "Confluence Shadow Ultra",
        "Confluence Probe Ultra",
    ]


def test_meta_set_agent_reassignment_updates_every_member_atomically(
    client,
    admin_headers,
    sample_agent,
    sample_model,
):
    prompt = _create_arena_prompt(client, admin_headers)
    created = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    ).json()
    replacement_response = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": "high",
        },
        headers=admin_headers,
    )
    assert replacement_response.status_code == 201, replacement_response.text
    replacement = replacement_response.json()

    response = client.patch(
        f"/api/admin/meta-portfolio-sets/{created['id']}",
        json={"agent_id": replacement["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["agent_id"] == replacement["id"]
    assert [portfolio["id"] for portfolio in updated["portfolios"]] == [
        portfolio["id"] for portfolio in created["portfolios"]
    ]

    with session_factory()() as session:
        meta_set = session.get(MetaPortfolioSet, created["id"])
        assert meta_set is not None
        assert meta_set.agent_id == replacement["id"]
        member_agent_ids = session.scalars(
            select(Portfolio.agent_id).where(Portfolio.meta_set_id == meta_set.id)
        ).all()
        assert member_agent_ids == [replacement["id"]] * 4


def test_meta_set_conflict_rolls_back_every_new_member(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    conflict = client.post(
        "/api/portfolios",
        json={
            "name": "Confluence Core",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert conflict.status_code == 201, conflict.text
    prompt = _create_arena_prompt(client, admin_headers)

    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 409

    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(MetaPortfolioSet)) == 0
        names = session.scalars(select(Portfolio.name).where(Portfolio.name.like("Confluence%"))).all()
        assert names == ["Confluence Core"]


def test_meta_set_requires_an_arena_four_cell_prompt(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Invalid Scope",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "arena-scoped" in response.json()["detail"]


def test_meta_set_requires_automation_capable_agent(client, admin_headers):
    model_response = client.post(
        "/api/models",
        json={"name": "Manual Meta Model", "capabilities": []},
        headers=admin_headers,
    )
    assert model_response.status_code == 201, model_response.text
    agent_response = client.post(
        "/api/agents",
        json={
            "model_id": model_response.json()["id"],
            "harness": None,
            "reasoning_effort": None,
        },
        headers=admin_headers,
    )
    assert agent_response.status_code == 201, agent_response.text
    prompt = _create_arena_prompt(client, admin_headers)

    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Manual Family",
            "agent_id": agent_response.json()["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "integrated automation" in response.json()["detail"]


def test_meta_set_member_cannot_be_deleted_individually(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_arena_prompt(client, admin_headers)
    created = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
        },
        headers=admin_headers,
    ).json()

    response = client.delete(
        f"/api/portfolios/{created['portfolios'][0]['id']}",
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert "cannot be deleted individually" in response.json()["detail"]
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Portfolio)) == 4
