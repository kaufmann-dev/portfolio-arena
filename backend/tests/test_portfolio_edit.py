"""Creating a portfolio with its fixed prompt, and editing name/agent/prompt/cost via PATCH."""


class TestCreatePortfolioPrompt:
    def test_create_requires_prompt(self, client, admin_headers, sample_agent):
        resp = client.post(
            "/api/portfolios",
            json={"name": "No Prompt", "agent_id": sample_agent["id"]},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # prompt_id is required

    def test_create_missing_prompt_rejected(self, client, admin_headers, sample_agent):
        resp = client.post(
            "/api/portfolios",
            json={"name": "Bad Prompt", "agent_id": sample_agent["id"], "prompt_id": 999999},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_created_portfolio_carries_prompt(self, client, sample_portfolio):
        row = next(
            p
            for p in client.get("/api/leaderboard").json()["portfolios"]
            if p["id"] == sample_portfolio["id"]
        )
        assert row["prompt"]["slug"] == "weekly-manager-v1"


class TestEditPortfolio:
    def test_patch_updates_name_agent_cost(self, client, admin_headers, sample_portfolio):
        other = client.post("/api/agents", json={"name": "GPT-5 (Codex)"}, headers=admin_headers).json()

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

        rows = client.get("/api/leaderboard").json()["portfolios"]
        row = next(p for p in rows if p["id"] == sample_portfolio["id"])
        assert row["name"] == "Renamed Weekly"
        assert row["agent"]["id"] == other["id"]
        assert row["cost_bps"] == 25

    def test_patch_changes_prompt(self, client, admin_headers, sample_portfolio):
        other = client.post(
            "/api/prompts",
            json={"name": "weekly-manager-v2", "text": "Be bolder."},
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
            for p in client.get("/api/leaderboard").json()["portfolios"]
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

    def test_patch_benchmark_forbidden(self, client, admin_headers):
        benchmark = next(p for p in client.get("/api/leaderboard").json()["portfolios"] if p["is_benchmark"])
        resp = client.patch(f"/api/portfolios/{benchmark['id']}", json={"name": "hax"}, headers=admin_headers)
        assert resp.status_code == 403
