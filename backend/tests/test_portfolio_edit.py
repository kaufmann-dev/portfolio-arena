"""Editing a portfolio's name, agent, and cost bps via PATCH."""


class TestEditPortfolio:
    def test_patch_updates_name_agent_cost(self, client, admin_headers, sample_portfolio):
        other = client.post(
            "/api/agents", json={"name": "GPT-5 (Codex)"}, headers=admin_headers
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

        rows = client.get("/api/leaderboard").json()["portfolios"]
        row = next(p for p in rows if p["id"] == sample_portfolio["id"])
        assert row["name"] == "Renamed Weekly"
        assert row["agent"]["id"] == other["id"]
        assert row["cost_bps"] == 25

    def test_patch_missing_agent_rejected(self, client, admin_headers, sample_portfolio):
        resp = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"agent_id": 999999},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_patch_benchmark_forbidden(self, client, admin_headers):
        benchmark = next(p for p in client.get("/api/leaderboard").json()["portfolios"] if p["is_benchmark"])
        resp = client.patch(
            f"/api/portfolios/{benchmark['id']}", json={"name": "hax"}, headers=admin_headers
        )
        assert resp.status_code == 403
