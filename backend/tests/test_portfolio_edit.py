"""Creating and editing portfolios with canonical prompts and execution modes."""


class TestCreatePortfolioPrompt:
    def test_create_requires_prompt(self, client, admin_headers, sample_agent):
        resp = client.post(
            "/api/portfolios",
            json={"name": "No Prompt", "agent_id": sample_agent["id"], "prompt_mode": "managed"},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # prompt_id is required

    def test_create_requires_prompt_mode(self, client, admin_headers, sample_agent, sample_prompt):
        resp = client.post(
            "/api/portfolios",
            json={
                "name": "No Mode",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_create_rejects_unknown_prompt_mode(self, client, admin_headers, sample_agent, sample_prompt):
        resp = client.post(
            "/api/portfolios",
            json={
                "name": "Bad Mode",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "hybrid",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_create_missing_prompt_rejected(self, client, admin_headers, sample_agent):
        resp = client.post(
            "/api/portfolios",
            json={
                "name": "Bad Prompt",
                "agent_id": sample_agent["id"],
                "prompt_id": 999999,
                "prompt_mode": "managed",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_create_reserves_only_the_synthetic_spy_identity(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
    ):
        base = {
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
        }
        reserved_name = client.post(
            "/api/portfolios",
            json={**base, "name": "SPY", "slug": "spy-contestant"},
            headers=admin_headers,
        )
        reserved_slug = client.post(
            "/api/portfolios",
            json={**base, "name": "S.P.Y", "slug": "spy"},
            headers=admin_headers,
        )
        reserved_derived_slug = client.post(
            "/api/portfolios",
            json={**base, "name": "SPY!"},
            headers=admin_headers,
        )
        assert reserved_name.status_code == 409
        assert reserved_slug.status_code == 409
        assert reserved_derived_slug.status_code == 409

        valid = client.post(
            "/api/portfolios",
            json={**base, "name": "SPY challenger"},
            headers=admin_headers,
        )
        assert valid.status_code == 201, valid.text
        assert valid.json()["slug"] == "spy-challenger"

    def test_created_portfolio_carries_prompt(self, client, sample_portfolio):
        row = next(
            p
            for p in client.get("/api/arena/managed").json()["portfolios"]
            if p["id"] == sample_portfolio["id"]
        )
        assert row["prompt"]["slug"] == "weekly-manager-v1"
        assert row["prompt_mode"] == "managed"


class TestEditPortfolio:
    def test_patch_updates_name_agent_cost(
        self,
        client,
        admin_headers,
        sample_portfolio,
        sample_model,
    ):
        other = client.post(
            "/api/agents",
            json={
                "model_id": sample_model["id"],
                "harness": None,
                "reasoning_effort": None,
            },
            headers=admin_headers,
        ).json()

        resp = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"name": "Renamed Weekly", "agent_id": other["id"], "cost_bps": 25},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Renamed Weekly"
        assert body["agent_id"] == other["id"]
        assert body["cost_bps"] == 25

        rows = client.get("/api/arena/managed").json()["portfolios"]
        row = next(p for p in rows if p["id"] == sample_portfolio["id"])
        assert row["name"] == "Renamed Weekly"
        assert row["agent"]["id"] == other["id"]
        assert row["cost_bps"] == 25

    def test_patch_reserves_only_the_synthetic_spy_display_name(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        reserved = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"name": " sPy "},
            headers=admin_headers,
        )
        assert reserved.status_code == 409

        valid = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"name": "SPY challenger"},
            headers=admin_headers,
        )
        assert valid.status_code == 200, valid.text
        assert valid.json()["name"] == "SPY challenger"

    def test_patch_requires_reset_before_changing_prompt_mode(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        blocked = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"prompt_mode": "rebuilt"},
            headers=admin_headers,
        )
        assert blocked.status_code == 409

        reset = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/reset",
            headers=admin_headers,
        )
        assert reset.status_code == 200, reset.text

        changed = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"prompt_mode": "rebuilt"},
            headers=admin_headers,
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["prompt_mode"] == "rebuilt"

    def test_patch_changes_prompt(self, client, admin_headers, sample_portfolio):
        other = client.post(
            "/api/prompts",
            json={
                "name": "weekly-manager-v2",
                "text": "Be bolder.",
                "allocation_policy": {
                    "min_position_weight_pct": 10,
                    "max_position_weight_pct": 25,
                },
            },
            headers=admin_headers,
        ).json()

        resp = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"prompt_id": other["id"]},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["prompt_id"] == other["id"]

        row = next(
            p
            for p in client.get("/api/arena/managed").json()["portfolios"]
            if p["id"] == sample_portfolio["id"]
        )
        assert row["prompt"]["id"] == other["id"]

    def test_patch_missing_agent_rejected(self, client, admin_headers, sample_portfolio):
        resp = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"agent_id": 999999},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_patch_missing_prompt_rejected(self, client, admin_headers, sample_portfolio):
        resp = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"prompt_id": 999999},
            headers=admin_headers,
        )
        assert resp.status_code == 422
