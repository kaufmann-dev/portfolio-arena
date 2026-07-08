"""Public reads: leaderboard, portfolio detail, compare, prompts, agents,
benchmark auto-seeding."""
from .util import backdate_allocation


def find(rows, slug):
    return next((row for row in rows if row["slug"] == slug), None)


class TestLeaderboard:
    def test_empty_arena(self, client):
        payload = client.get("/api/leaderboard").json()
        # Benchmarks exist but have no allocations until a real portfolio does.
        slugs = {row["slug"] for row in payload["portfolios"]}
        assert {"spy-buy-and-hold", "rsp-buy-and-hold"} <= slugs
        assert all(not row["metrics"]["has_data"] for row in payload["portfolios"])

    def test_portfolio_with_history(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/leaderboard").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row is not None
        assert row["metrics"]["has_data"]
        assert row["metrics"]["itd_return"] is not None
        assert row["metrics"]["vs_spy"] is not None
        assert row["too_early"] is True  # 45 days < 6 months
        assert len(row["sparkline"]) > 5
        assert row["prompt"]["slug"] == "weekly-manager-v1"

    def test_benchmarks_seeded_at_same_inception(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/leaderboard").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        spy = find(payload["portfolios"], "spy-buy-and-hold")
        assert spy["is_benchmark"] is True
        assert spy["metrics"]["has_data"]
        assert spy["inception"] == row["inception"]
        # Benchmarks are cost-free: SPY tracks itself exactly.
        assert spy["metrics"]["vs_spy"] == 0 or abs(spy["metrics"]["vs_spy"]) < 1e-9
        assert spy["cost_bps"] == 0

    def test_pending_allocation_has_no_data(self, client, sample_portfolio):
        payload = client.get("/api/leaderboard").json()
        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row["metrics"]["has_data"] is False


class TestPortfolioDetail:
    def test_detail_shape(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()

        portfolio = payload["portfolio"]
        assert portfolio["series"], "expected a NAV series"
        assert portfolio["spy_series"][0]["nav"] == 100.0
        assert portfolio["series"][0]["date"] == portfolio["spy_series"][0]["date"]

        symbols = {holding["symbol"] for holding in portfolio["holdings"]}
        assert symbols == {"AAPL", "CASH:USD"}

        allocation = portfolio["allocations"][0]
        assert allocation["locked"] is True
        assert allocation["prompt"]["slug"] == "weekly-manager-v1"
        assert allocation["applied_date"] is not None
        assert allocation["cost"] is not None

    def test_404(self, client):
        assert client.get("/api/portfolios/nope").status_code == 404


class TestCompare:
    def test_overlay_rebased_to_common_start(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        response = client.get(
            f"/api/compare?slugs={sample_portfolio['slug']},spy-buy-and-hold"
        )
        payload = response.json()
        assert len(payload["series"]) == 2
        for entry in payload["series"]:
            assert entry["series"][0]["nav"] == 100.0
            assert entry["series"][0]["date"] == payload["start"]

    def test_bad_params(self, client):
        assert client.get("/api/compare?slugs=").status_code == 422


class TestPromptsAndAgents:
    def test_prompt_detail_lists_portfolios(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/prompts/weekly-manager-v1").json()
        assert payload["prompt"]["text"].startswith("Manage a portfolio")
        assert find(payload["portfolios"], sample_portfolio["slug"]) is not None

    def test_agent_detail_lists_portfolios(self, client, sample_portfolio, sample_agent):
        payload = client.get(f"/api/agents/{sample_agent['slug']}").json()
        assert find(payload["portfolios"], sample_portfolio["slug"]) is not None

    def test_prompt_editing_reflected_publicly(self, client, admin_headers, sample_prompt):
        client.patch(
            f"/api/prompts/{sample_prompt['id']}",
            json={"text": "Updated instructions."},
            headers=admin_headers,
        )
        payload = client.get(f"/api/prompts/{sample_prompt['slug']}").json()
        assert payload["prompt"]["text"] == "Updated instructions."


class TestAdminMisc:
    def test_settings_roundtrip(self, client, admin_headers):
        assert client.get("/api/settings", headers=admin_headers).json() == {"default_cost_bps": 10}
        client.put("/api/settings", json={"default_cost_bps": 25}, headers=admin_headers)
        assert client.get("/api/settings", headers=admin_headers).json() == {"default_cost_bps": 25}

    def test_clear_price_cache(self, client, admin_headers, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        client.get("/api/leaderboard")  # populates the cache
        response = client.delete("/api/prices/cache", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["deleted"] >= 1

    def test_portfolio_rename_and_archive(self, client, admin_headers, sample_portfolio):
        response = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"name": "Renamed", "status": "archived"},
            headers=admin_headers,
        )
        assert response.json()["name"] == "Renamed"
        assert response.json()["status"] == "archived"

    def test_symbol_search(self, client, admin_headers):
        response = client.get("/api/symbols/search?q=AAP", headers=admin_headers)
        assert response.status_code == 200
        assert any(item["symbol"] == "AAPL" for item in response.json()["results"])
