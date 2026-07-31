"""Delete endpoints for agents, prompts, and portfolios, including
reference guards (agent/prompt in use → 409) and cascade behavior."""


class TestDeleteAgent:
    def test_delete_unused_agent(self, client, admin_headers, sample_agent):
        response = client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        listing = client.get("/api/agents").json()["agents"]
        assert all(a["id"] != sample_agent["id"] for a in listing)

    def test_delete_agent_in_use_blocked(self, client, admin_headers, sample_portfolio, sample_agent):
        response = client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers)
        assert response.status_code == 409
        assert "portfolio" in response.json()["detail"].lower()

    def test_delete_missing_agent(self, client, admin_headers):
        assert client.delete("/api/agents/999999", headers=admin_headers).status_code == 404


class TestDeletePrompt:
    def test_delete_unused_prompt(self, client, admin_headers, sample_prompt):
        response = client.delete(f"/api/prompts/{sample_prompt['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        listing = client.get("/api/prompts").json()["prompts"]
        assert all(p["id"] != sample_prompt["id"] for p in listing)

    def test_delete_prompt_in_use_blocked(self, client, admin_headers, sample_portfolio, sample_prompt):
        # sample_portfolio uses sample_prompt as its fixed prompt.
        response = client.delete(f"/api/prompts/{sample_prompt['id']}", headers=admin_headers)
        assert response.status_code == 409
        assert "portfolio" in response.json()["detail"].lower()

    def test_delete_missing_prompt(self, client, admin_headers):
        assert client.delete("/api/prompts/999999", headers=admin_headers).status_code == 404


class TestDeletePortfolio:
    def test_delete_portfolio_cascades(self, client, admin_headers, sample_portfolio):
        response = client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert client.get(f"/api/portfolios/{sample_portfolio['slug']}").status_code == 404
        rows = client.get("/api/arena/managed").json()["portfolios"]
        assert all(p["id"] != sample_portfolio["id"] for p in rows)

    def test_delete_last_portfolio_leaves_only_synthetic_spy(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        seeded = client.get("/api/arena/managed").json()["portfolios"]
        assert seeded[0]["kind"] == "benchmark"
        assert seeded[0]["metrics"]["has_data"] is True

        response = client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text

        rows = client.get("/api/arena/managed").json()["portfolios"]
        assert len(rows) == 1
        assert rows[0]["kind"] == "benchmark"
        assert rows[0]["id"] is None
        assert rows[0]["metrics"]["has_data"] is False

    def test_synthetic_spy_is_not_an_addressable_portfolio(self, client, admin_headers):
        assert client.get("/api/portfolios/spy").status_code == 404
        assert client.delete("/api/portfolios/spy", headers=admin_headers).status_code == 422

    def test_delete_missing_portfolio(self, client, admin_headers):
        assert client.delete("/api/portfolios/999999", headers=admin_headers).status_code == 404
